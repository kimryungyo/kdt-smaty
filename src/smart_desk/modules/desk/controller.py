"""목표 이동, 수동 HOLD와 안전 STOP을 한 곳에서 관리한다."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
import logging
import math
import time
from typing import TypeAlias

from smart_desk.config.settings import DeskSettings
from smart_desk.core.task_manager import TaskManager
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


LOGGER = logging.getLogger(__name__)
DESK_CONTROLLER_TASK_NAME = "desk-controller"
SUPPORTED_RELAY_FIRMWARES = frozenset({"smartdesk-fin-relay-1.0.0"})

Now: TypeAlias = Callable[[], datetime]
Monotonic: TypeAlias = Callable[[], float]
WaitForWake: TypeAlias = Callable[[asyncio.Event, float], Awaitable[bool]]


def utc_now() -> datetime:
    """현재 timezone-aware UTC 시각을 반환한다."""

    return datetime.now(UTC)


async def wait_for_event_or_timeout(event: asyncio.Event, timeout: float) -> bool:
    """event 또는 timeout 중 먼저 발생한 조건까지 기다린다."""

    try:
        async with asyncio.timeout(timeout):
            await event.wait()
    except TimeoutError:
        return False
    event.clear()
    return True


class DeskCommandRejectedError(RuntimeError):
    """형식은 유효하지만 현재 안전 상태에서 실행할 수 없는 명령."""


class _MotionMode(StrEnum):
    NONE = "NONE"
    TARGET = "TARGET"
    MANUAL = "MANUAL"


class _TargetPhase(StrEnum):
    CONTINUOUS = "CONTINUOUS"
    SETTLING = "SETTLING"
    FINE = "FINE"


@dataclass(frozen=True, slots=True)
class _ControlState:
    public_state: DeskState
    mode: _MotionMode
    target_phase: _TargetPhase | None
    target_height_cm: float | None
    direction: Direction | None
    detail: str
    last_error: str | None
    updated_at: datetime
    generation: int


class DeskController:
    """센서와 릴레이 snapshot을 조합해 안전한 이동 의도를 실행한다."""

    def __init__(
        self,
        height_monitor: DeskHeightMonitor,
        relay: RelayClient,
        settings: DeskSettings,
        task_manager: TaskManager,
        *,
        now: Now = utc_now,
        monotonic: Monotonic = time.monotonic,
        wait_for_wake: WaitForWake = wait_for_event_or_timeout,
    ) -> None:
        self._height_monitor = height_monitor
        self._relay = relay
        self._settings = settings
        self._task_manager = task_manager
        self._now = now
        self._monotonic = monotonic
        self._wait_for_wake = wait_for_wake
        initial_now = self._require_utc(now())
        self._control = _ControlState(
            public_state=DeskState.IDLE,
            mode=_MotionMode.NONE,
            target_phase=None,
            target_height_cm=None,
            direction=None,
            detail="책상 제어기가 시작되지 않았습니다.",
            last_error=None,
            updated_at=initial_now,
            generation=0,
        )
        self._running = False
        self._closing = False
        self._stop_in_progress = False
        self._runner_task: asyncio.Task[None] | None = None
        self._intent_lock = asyncio.Lock()
        self._command_lock = asyncio.Lock()
        self._relay_io_lock = asyncio.Lock()
        self._wake_event = asyncio.Event()
        self._motion_started_at: float | None = None
        self._last_hold_at: float | None = None
        self._next_refresh_at: float | None = None
        self._settle_until: float | None = None
        self._last_fine_observed_at: datetime | None = None
        self._relay_baseline_received_at: datetime | None = None
        self._relay_last_seen_at: datetime | None = None
        self._first_pulse_sent_at: float | None = None
        self._relay_direction_confirmed = False
        self._last_unexpected_relay_at: datetime | None = None

    async def start(self) -> None:
        """제어 runner를 시작하고 UP/DOWN 없이 안전 STOP을 한 번 보낸다."""

        async with self._command_lock:
            if self._running or (
                self._runner_task is not None and not self._runner_task.done()
            ):
                raise RuntimeError("책상 제어기가 이미 실행 중입니다.")
            self._running = True
            self._closing = False
            self._stop_in_progress = True
            self._reset_motion_fields()
            self._control = _ControlState(
                public_state=DeskState.IDLE,
                mode=_MotionMode.NONE,
                target_phase=None,
                target_height_cm=None,
                direction=None,
                detail="책상 제어기를 시작하고 있습니다.",
                last_error=None,
                updated_at=self._require_utc(self._now()),
                generation=self._control.generation + 1,
            )
            try:
                self._runner_task = self._task_manager.create(
                    DESK_CONTROLLER_TASK_NAME,
                    self._run(),
                    critical=True,
                )
            except Exception:
                self._running = False
                self._stop_in_progress = False
                raise

        error = await self._send_stop_bounded()
        async with self._command_lock:
            self._stop_in_progress = False
            if error is None:
                self._control = replace(
                    self._control,
                    public_state=DeskState.IDLE,
                    detail="책상 제어기가 안전 정지 상태로 시작되었습니다.",
                    updated_at=self._require_utc(self._now()),
                )
            else:
                self._control = replace(
                    self._control,
                    public_state=DeskState.ERROR,
                    detail="시작 안전 정지 명령을 발행하지 못했습니다.",
                    last_error=error,
                    updated_at=self._require_utc(self._now()),
                )
        self._wake_event.set()

    async def stop(self) -> None:
        """새 명령을 차단하고 final STOP 뒤 runner를 종료한다."""

        async with self._command_lock:
            if not self._running and self._runner_task is None:
                return
            self._closing = True
            self._stop_in_progress = True
            self._invalidate_motion_locked("애플리케이션 종료")
        self._wake_event.set()

        error = await self._send_stop_bounded()
        task = self._runner_task
        self._runner_task = None
        if task is not None:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        async with self._command_lock:
            self._running = False
            self._stop_in_progress = False
            self._control = replace(
                self._control,
                public_state=DeskState.STOPPED if error is None else DeskState.ERROR,
                detail=(
                    "책상 제어기가 종료되었습니다."
                    if error is None
                    else "종료 중 안전 정지 명령을 발행하지 못했습니다."
                ),
                last_error=error,
                updated_at=self._require_utc(self._now()),
            )
        if error is not None:
            raise RuntimeError(error)

    async def stop_motion(self, reason: str = "") -> None:
        """runner는 유지한 채 모든 현재 이동 의도를 안전하게 취소한다."""

        async with self._intent_lock:
            await self._stop_motion(
                reason.strip() or "사용자 요청으로 책상을 정지했습니다.",
                error_detail=None,
            )

    async def set_target(self, height_cm: float) -> None:
        """검증된 높이를 새 자동 이동 목표로 설정한다."""

        async with self._intent_lock:
            await self._set_target(height_cm)

    async def _set_target(self, height_cm: float) -> None:
        try:
            target = self._validate_number(height_cm, "목표 높이")
            if not self._settings.operation_min_cm <= target <= self._settings.operation_max_cm:
                raise ValueError(
                    f"목표 높이는 {self._settings.operation_min_cm:g}~"
                    f"{self._settings.operation_max_cm:g}cm여야 합니다."
                )
        except (TypeError, ValueError):
            if await self._has_active_motion():
                await self._stop_motion(
                    "잘못된 대체 목표로 기존 이동을 정지했습니다.",
                    error_detail=None,
                )
            raise

        async with self._command_lock:
            if (
                self._control.mode is _MotionMode.TARGET
                and self._control.target_height_cm == target
                and not self._stop_in_progress
            ):
                return
            active = self._control.mode is not _MotionMode.NONE

        relay_snapshot = self._relay.get_snapshot()
        if active or relay_snapshot.state in {RelayState.UP, RelayState.DOWN}:
            await self._stop_for_transition("새 목표를 적용하기 위해 기존 이동을 정지했습니다.")

        height, relay_snapshot = self._admit_motion()
        assert height.height_cm is not None
        delta = target - height.height_cm
        if abs(delta) <= self._settings.target_tolerance_cm:
            await self._stop_motion(
                f"목표 {target:.1f}cm에 도달했습니다.",
                error_detail=None,
            )
            return
        direction = Direction.UP if delta > 0 else Direction.DOWN
        self._require_direction_allowed(height.height_cm, direction)

        async with self._command_lock:
            self._require_running_locked()
            generation = self._control.generation + 1
            self._control = _ControlState(
                public_state=DeskState.MOVING,
                mode=_MotionMode.TARGET,
                target_phase=_TargetPhase.CONTINUOUS,
                target_height_cm=target,
                direction=direction,
                detail=f"목표 {target:.1f}cm로 이동합니다.",
                last_error=None,
                updated_at=self._require_utc(self._now()),
                generation=generation,
            )
            now_mono = self._monotonic()
            self._motion_started_at = now_mono
            self._next_refresh_at = now_mono
            self._last_hold_at = None
            self._settle_until = None
            self._last_fine_observed_at = None
            self._initialize_relay_tracking(relay_snapshot)
        self._wake_event.set()

    async def increase_target(self, amount_cm: float) -> None:
        """활성 목표를 지정 값만큼 높인다."""

        async with self._intent_lock:
            await self._adjust_target(amount_cm, increase=True)

    async def decrease_target(self, amount_cm: float) -> None:
        """활성 목표를 지정 값만큼 낮춘다."""

        async with self._intent_lock:
            await self._adjust_target(amount_cm, increase=False)

    async def hold_up(self) -> None:
        """최근 호출이 유지되는 동안 수동 상승 의도를 갱신한다."""

        async with self._intent_lock:
            await self._hold(Direction.UP)

    async def hold_down(self) -> None:
        """최근 호출이 유지되는 동안 수동 하강 의도를 갱신한다."""

        async with self._intent_lock:
            await self._hold(Direction.DOWN)

    def get_snapshot(self) -> DeskSnapshot:
        """I/O 없이 현재 제어·높이·릴레이 상태를 반환한다."""

        control = self._control
        return DeskSnapshot(
            state=control.public_state,
            height=self._height_monitor.get_snapshot(),
            relay=self._relay.get_snapshot(),
            target_height_cm=control.target_height_cm,
            direction=control.direction,
            detail=control.detail,
            last_error=control.last_error,
            updated_at=control.updated_at,
        )

    async def _adjust_target(self, amount_cm: float, *, increase: bool) -> None:
        try:
            amount = self._validate_number(amount_cm, "목표 증감량")
            if amount <= 0:
                raise ValueError("목표 증감량은 0보다 커야 합니다.")
        except (TypeError, ValueError):
            if await self._has_active_motion():
                await self._stop_motion(
                    "잘못된 목표 증감 요청으로 기존 이동을 정지했습니다.",
                    error_detail=None,
                )
            raise

        async with self._command_lock:
            target = self._control.target_height_cm
            if self._control.mode is not _MotionMode.TARGET or target is None:
                raise DeskCommandRejectedError("활성 목표가 없어 높이를 증감할 수 없습니다.")
        await self._set_target(target + amount if increase else target - amount)

    async def _hold(self, direction: Direction) -> None:
        if not self._running or self._closing:
            raise RuntimeError("책상 제어기가 실행 중이 아닙니다.")
        if self._stop_in_progress:
            raise DeskCommandRejectedError("STOP 명령이 진행 중입니다.")
        height = self._require_height()
        relay_snapshot = self._require_relay_ready()
        assert height.height_cm is not None
        self._require_direction_allowed(height.height_cm, direction)

        async with self._command_lock:
            self._require_running_locked()
            if (
                self._control.mode is _MotionMode.MANUAL
                and self._control.direction is direction
                and not self._stop_in_progress
            ):
                self._last_hold_at = self._monotonic()
                self._control = replace(
                    self._control,
                    detail=f"수동 {direction.value} 입력을 유지하고 있습니다.",
                    updated_at=self._require_utc(self._now()),
                )
                self._wake_event.set()
                return
            active = self._control.mode is not _MotionMode.NONE

        if active or relay_snapshot.state in {RelayState.UP, RelayState.DOWN}:
            await self._stop_for_transition("수동 이동 전 기존 이동을 정지했습니다.")
            height, relay_snapshot = self._admit_motion()
            assert height.height_cm is not None
            self._require_direction_allowed(height.height_cm, direction)

        async with self._command_lock:
            self._require_running_locked()
            now_mono = self._monotonic()
            self._control = _ControlState(
                public_state=DeskState.MANUAL,
                mode=_MotionMode.MANUAL,
                target_phase=None,
                target_height_cm=None,
                direction=direction,
                detail=f"수동 {direction.value} 이동을 시작합니다.",
                last_error=None,
                updated_at=self._require_utc(self._now()),
                generation=self._control.generation + 1,
            )
            self._motion_started_at = now_mono
            self._last_hold_at = now_mono
            self._next_refresh_at = now_mono
            self._settle_until = None
            self._last_fine_observed_at = None
            self._initialize_relay_tracking(relay_snapshot)
        self._wake_event.set()

    async def _run(self) -> None:
        try:
            while self._running:
                await self._run_cycle()
                timeout = self._settings.control_poll_interval_seconds
                await self._wait_for_wake(self._wake_event, timeout)
        except asyncio.CancelledError:
            if not self._closing:
                await asyncio.shield(
                    self._stop_motion(
                        "책상 제어 runner 취소로 정지했습니다.",
                        error_detail="책상 제어 runner가 lifecycle 밖에서 취소되었습니다.",
                    )
                )
            raise
        except Exception as error:
            LOGGER.exception(
                "책상 제어 runner가 예기치 않게 종료되었습니다.",
                extra={"component": "desk", "event": "desk_runner_failed"},
            )
            if not self._closing:
                await self._stop_motion(
                    "책상 제어 runner 오류로 정지했습니다.",
                    error_detail=str(error),
                )
            raise

    async def _run_cycle(self) -> None:
        async with self._command_lock:
            control = self._control
            if not self._running or self._closing or self._stop_in_progress:
                return

        if control.mode is _MotionMode.NONE:
            await self._watch_inactive_relay()
            return

        try:
            height = self._require_height()
            relay_snapshot = self._require_relay_ready()
            assert height.height_cm is not None
            now_mono = self._monotonic()
            if (
                control.mode is _MotionMode.TARGET
                and self._motion_started_at is not None
                and now_mono - self._motion_started_at
                > self._settings.target_timeout_seconds
            ):
                raise DeskCommandRejectedError("목표 이동 제한시간을 초과했습니다.")
            if (
                control.mode is _MotionMode.MANUAL
                and self._last_hold_at is not None
                and now_mono - self._last_hold_at
                > self._settings.manual_watchdog_seconds
            ):
                await self._stop_motion(
                    "수동 HOLD 입력이 끊겨 정지했습니다.",
                    error_detail=None,
                )
                return

            await self._evaluate_relay_status(control, relay_snapshot, now_mono)
            if control.mode is _MotionMode.MANUAL:
                assert control.direction is not None
                self._require_direction_allowed(height.height_cm, control.direction)
                if self._next_refresh_at is None or now_mono >= self._next_refresh_at:
                    await self._publish_pulse(
                        control.generation,
                        control.direction,
                        self._settings.manual_hold_ms,
                    )
                return
            await self._run_target_cycle(control, height, now_mono)
        except DeskCommandRejectedError as error:
            await self._stop_motion(
                "안전 조건을 충족하지 못해 이동을 정지했습니다.",
                error_detail=str(error),
            )

    async def _run_target_cycle(
        self,
        control: _ControlState,
        height: HeightSnapshot,
        now_mono: float,
    ) -> None:
        assert control.target_height_cm is not None
        assert height.height_cm is not None
        delta = control.target_height_cm - height.height_cm
        if abs(delta) <= self._settings.target_tolerance_cm:
            await self._stop_motion(
                f"목표 {control.target_height_cm:.1f}cm에 도달했습니다.",
                error_detail=None,
            )
            return

        needed = Direction.UP if delta > 0 else Direction.DOWN
        self._require_direction_allowed(height.height_cm, needed)
        if control.target_phase is _TargetPhase.SETTLING:
            if self._settle_until is not None and now_mono < self._settle_until:
                return
            relay = self._relay.get_snapshot()
            if relay.state is not RelayState.STOP:
                raise DeskCommandRejectedError("미세 이동 전 릴레이 STOP을 확인하지 못했습니다.")
            async with self._command_lock:
                if self._control.generation != control.generation:
                    return
                self._control = replace(
                    self._control,
                    target_phase=_TargetPhase.FINE,
                    direction=needed,
                    updated_at=self._require_utc(self._now()),
                )
                self._next_refresh_at = now_mono
                control = self._control

        if control.target_phase is _TargetPhase.CONTINUOUS:
            if (
                abs(delta) <= self._settings.fine_approach_distance_cm
                or control.direction is not needed
            ):
                await self._enter_settling(control.generation)
                return
            if self._next_refresh_at is None or now_mono >= self._next_refresh_at:
                await self._publish_pulse(
                    control.generation,
                    needed,
                    self._settings.continuous_hold_ms,
                )
            return

        if control.target_phase is _TargetPhase.FINE:
            if (
                self._last_fine_observed_at is not None
                and height.observed_at is not None
                and height.observed_at <= self._last_fine_observed_at
            ):
                return
            await self._publish_pulse(
                control.generation,
                needed,
                self._settings.fine_hold_ms,
                fine_observed_at=height.observed_at,
            )

    async def _enter_settling(self, generation: int) -> None:
        async with self._command_lock:
            if self._control.generation != generation:
                return
            new_generation = generation + 1
            self._control = replace(
                self._control,
                target_phase=_TargetPhase.SETTLING,
                direction=None,
                generation=new_generation,
                detail="목표 근처에서 관성 안정화를 기다립니다.",
                updated_at=self._require_utc(self._now()),
            )
            self._next_refresh_at = None
            self._relay_direction_confirmed = False
        error = await self._send_stop_bounded()
        if error is not None:
            await self._stop_motion(
                "미세 접근 전 정지 명령에 실패했습니다.",
                error_detail=error,
            )
            return
        self._settle_until = self._monotonic() + self._settings.fine_settle_seconds

    async def _publish_pulse(
        self,
        generation: int,
        direction: Direction,
        hold_ms: int,
        *,
        fine_observed_at: datetime | None = None,
    ) -> None:
        started_at = self._monotonic()
        publish_error: str | None = None
        async with self._relay_io_lock:
            async with self._command_lock:
                if (
                    self._control.generation != generation
                    or self._control.mode is _MotionMode.NONE
                    or self._control.direction is not direction
                    or self._closing
                    or self._stop_in_progress
                ):
                    return
            try:
                async with asyncio.timeout(self._settings.relay_ack_timeout_seconds):
                    await self._relay.pulse(direction, hold_ms)
            except Exception as error:
                publish_error = str(error)

        if publish_error is not None:
            await self._record_pulse_failure(generation, publish_error)
            return

        async with self._command_lock:
            if self._control.generation != generation:
                return
            if self._first_pulse_sent_at is None:
                self._first_pulse_sent_at = started_at
            self._next_refresh_at = (
                started_at + self._settings.pulse_refresh_interval_seconds
            )
            if fine_observed_at is not None:
                self._last_fine_observed_at = fine_observed_at
                self._control = replace(
                    self._control,
                    target_phase=_TargetPhase.SETTLING,
                    direction=None,
                    detail="미세 보정 뒤 새 높이를 기다립니다.",
                    updated_at=self._require_utc(self._now()),
                )
                self._settle_until = (
                    self._monotonic()
                    + hold_ms / 1000
                    + self._settings.fine_settle_seconds
                )

    async def _record_pulse_failure(self, generation: int, detail: str) -> None:
        async with self._command_lock:
            if self._control.generation != generation:
                return
        await self._stop_motion(
            "릴레이 이동 명령 발행에 실패했습니다.",
            error_detail=detail,
        )

    async def _evaluate_relay_status(
        self,
        control: _ControlState,
        snapshot: RelaySnapshot,
        now_mono: float,
    ) -> None:
        if control.direction is None:
            return
        received_at = snapshot.received_at
        if received_at is None:
            return
        if (
            self._relay_baseline_received_at is not None
            and received_at <= self._relay_baseline_received_at
        ):
            if (
                self._first_pulse_sent_at is not None
                and now_mono - self._first_pulse_sent_at
                > self._settings.relay_ack_timeout_seconds
            ):
                raise DeskCommandRejectedError("릴레이 이동 응답 시간이 초과되었습니다.")
            return
        if self._relay_last_seen_at is not None and received_at <= self._relay_last_seen_at:
            return
        self._relay_last_seen_at = received_at

        expected = RelayState(control.direction.value)
        if snapshot.state is expected:
            self._relay_direction_confirmed = True
            return
        if snapshot.state in {RelayState.UP, RelayState.DOWN}:
            raise DeskCommandRejectedError("릴레이가 요청과 반대 방향을 보고했습니다.")
        if snapshot.state is RelayState.STOP and self._relay_direction_confirmed:
            raise DeskCommandRejectedError("이동 중 릴레이가 예기치 않게 정지했습니다.")
        if (
            snapshot.state is RelayState.STOP
            and self._first_pulse_sent_at is not None
            and now_mono - self._first_pulse_sent_at
            > self._settings.relay_ack_timeout_seconds
        ):
            raise DeskCommandRejectedError("릴레이 이동 응답 시간이 초과되었습니다.")

    async def _watch_inactive_relay(self) -> None:
        snapshot = self._relay.get_snapshot()
        if snapshot.state not in {RelayState.UP, RelayState.DOWN}:
            return
        if snapshot.received_at is None or snapshot.received_at == self._last_unexpected_relay_at:
            return
        try:
            self._require_relay_ready()
        except DeskCommandRejectedError:
            pass
        self._last_unexpected_relay_at = snapshot.received_at
        await self._stop_motion(
            "제어기 밖에서 발생한 릴레이 이동을 정지했습니다.",
            error_detail="예기치 않은 릴레이 이동 상태",
        )

    async def _stop_motion(self, detail: str, *, error_detail: str | None) -> None:
        async with self._command_lock:
            if not self._running and not self._closing:
                raise RuntimeError("책상 제어기가 실행 중이 아닙니다.")
            if self._stop_in_progress:
                return
            self._stop_in_progress = True
            self._invalidate_motion_locked(detail)
        self._wake_event.set()
        publish_error = await self._send_stop_bounded()
        final_error = error_detail
        if publish_error is not None:
            final_error = (
                f"{error_detail}; STOP 실패: {publish_error}"
                if error_detail
                else publish_error
            )
        async with self._command_lock:
            self._stop_in_progress = False
            self._control = replace(
                self._control,
                public_state=(
                    DeskState.ERROR if final_error is not None else DeskState.STOPPED
                ),
                detail=detail,
                last_error=final_error,
                updated_at=self._require_utc(self._now()),
            )
        if publish_error is not None and error_detail is None:
            raise RuntimeError(publish_error)

    async def _stop_for_transition(self, detail: str) -> None:
        baseline = self._relay.get_snapshot().received_at
        async with self._command_lock:
            self._require_running_locked()
            if self._stop_in_progress:
                raise DeskCommandRejectedError("다른 STOP 명령이 진행 중입니다.")
            self._stop_in_progress = True
            self._invalidate_motion_locked(detail)
        error = await self._send_stop_bounded()
        if error is None:
            error = await self._wait_for_fresh_stop(baseline)
        async with self._command_lock:
            self._stop_in_progress = False
            self._control = replace(
                self._control,
                public_state=DeskState.ERROR if error else DeskState.STOPPED,
                last_error=error,
                updated_at=self._require_utc(self._now()),
            )
        if error is not None:
            raise DeskCommandRejectedError(error)

    async def _wait_for_fresh_stop(self, baseline: datetime | None) -> str | None:
        deadline = self._monotonic() + self._settings.relay_ack_timeout_seconds
        while self._monotonic() <= deadline:
            snapshot = self._relay.get_snapshot()
            if (
                snapshot.state is RelayState.STOP
                and snapshot.received_at is not None
                and (baseline is None or snapshot.received_at > baseline)
                and snapshot.event not in {RelayEvent.OFFLINE, RelayEvent.REJECTED}
            ):
                return None
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                break
            await self._wait_for_wake(
                self._wake_event,
                min(self._settings.control_poll_interval_seconds, remaining),
            )
        return "릴레이의 최신 STOP 응답을 확인하지 못했습니다."

    async def _send_stop_bounded(self) -> str | None:
        try:
            async with self._relay_io_lock:
                async with asyncio.timeout(self._settings.relay_ack_timeout_seconds):
                    await self._relay.send_stop()
        except Exception as error:
            return str(error)
        return None

    def _admit_motion(self) -> tuple[HeightSnapshot, RelaySnapshot]:
        if not self._running or self._closing:
            raise RuntimeError("책상 제어기가 실행 중이 아닙니다.")
        if self._stop_in_progress:
            raise DeskCommandRejectedError("STOP 명령이 진행 중입니다.")
        height = self._require_height()
        relay = self._require_relay_ready()
        if relay.state is not RelayState.STOP:
            raise DeskCommandRejectedError("릴레이가 STOP 상태가 아닙니다.")
        return height, relay

    def _require_height(self) -> HeightSnapshot:
        snapshot = self._height_monitor.get_snapshot()
        if (
            snapshot.status is not HeightStatus.ONLINE
            or snapshot.height_cm is None
            or snapshot.observed_at is None
            or not math.isfinite(snapshot.height_cm)
            or not self._settings.measurement_min_cm
            <= snapshot.height_cm
            <= self._settings.measurement_max_cm
        ):
            raise DeskCommandRejectedError("신선하고 유효한 현재 높이가 없습니다.")
        return snapshot

    def _require_relay_ready(self) -> RelaySnapshot:
        snapshot = self._relay.get_snapshot()
        if snapshot.last_error is not None:
            raise DeskCommandRejectedError("릴레이 상태 payload가 유효하지 않습니다.")
        if snapshot.event in {RelayEvent.OFFLINE, RelayEvent.REJECTED}:
            raise DeskCommandRejectedError(
                snapshot.code or "릴레이가 이동 명령을 받을 수 없습니다."
            )
        if snapshot.firmware not in SUPPORTED_RELAY_FIRMWARES:
            raise DeskCommandRejectedError("승인되지 않은 릴레이 펌웨어입니다.")
        if snapshot.received_at is None:
            raise DeskCommandRejectedError("릴레이 live 상태를 아직 받지 못했습니다.")
        received_at = self._require_utc(snapshot.received_at)
        age = self._require_utc(self._now()) - received_at
        if age < timedelta(0) or age > timedelta(
            seconds=self._settings.relay_stale_after_seconds
        ):
            raise DeskCommandRejectedError("릴레이 상태가 오래됐습니다.")
        if snapshot.code in {"height_waiting", "height_not_ready", "height_stale"}:
            raise DeskCommandRejectedError("펌웨어의 높이 안전 lease가 준비되지 않았습니다.")
        return snapshot

    def _require_direction_allowed(self, height_cm: float, direction: Direction) -> None:
        if direction is Direction.UP and height_cm >= self._settings.operation_max_cm:
            raise DeskCommandRejectedError("제어 상한에서는 위로 이동할 수 없습니다.")
        if direction is Direction.DOWN and height_cm <= self._settings.operation_min_cm:
            raise DeskCommandRejectedError("제어 하한에서는 아래로 이동할 수 없습니다.")

    async def _has_active_motion(self) -> bool:
        async with self._command_lock:
            return self._control.mode is not _MotionMode.NONE

    def _require_running_locked(self) -> None:
        if not self._running or self._closing:
            raise RuntimeError("책상 제어기가 실행 중이 아닙니다.")
        if self._stop_in_progress:
            raise DeskCommandRejectedError("STOP 명령이 진행 중입니다.")

    def _invalidate_motion_locked(self, detail: str) -> None:
        self._control = _ControlState(
            public_state=DeskState.STOPPED,
            mode=_MotionMode.NONE,
            target_phase=None,
            target_height_cm=None,
            direction=None,
            detail=detail,
            last_error=None,
            updated_at=self._require_utc(self._now()),
            generation=self._control.generation + 1,
        )
        self._reset_motion_fields()

    def _reset_motion_fields(self) -> None:
        self._motion_started_at = None
        self._last_hold_at = None
        self._next_refresh_at = None
        self._settle_until = None
        self._last_fine_observed_at = None
        self._relay_baseline_received_at = None
        self._relay_last_seen_at = None
        self._first_pulse_sent_at = None
        self._relay_direction_confirmed = False

    def _initialize_relay_tracking(self, snapshot: RelaySnapshot) -> None:
        self._relay_baseline_received_at = snapshot.received_at
        self._relay_last_seen_at = snapshot.received_at
        self._first_pulse_sent_at = None
        self._relay_direction_confirmed = False

    @staticmethod
    def _validate_number(value: float, label: str) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError(f"{label}은 bool이 아닌 숫자여야 합니다.")
        converted = float(value)
        if not math.isfinite(converted):
            raise ValueError(f"{label}은 finite 숫자여야 합니다.")
        return converted

    @staticmethod
    def _require_utc(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("책상 제어 시각은 timezone-aware UTC여야 합니다.")
        return value
