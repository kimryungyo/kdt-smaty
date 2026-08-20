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
SUPPORTED_RELAY_FIRMWARES = frozenset(
    {
        # 높이 relay와 틸트를 한 대가 맡는 통합 ESP32 펌웨어.
        "smartdesk-fin-desk-1.0.0",
        # ESP32 대신 라즈베리파이 GPIO가 직접 릴레이를 구동하는 구성.
        "rpi-gpio-relay-1.0.0",
        "smartdesk-fin-relay-1.0.0",
        "smartdesk-fin-relay-1.0.1",
        "smartdesk-fin-relay-1.0.2",
        "smartdesk-fin-relay-1.0.3",
        "smartdesk-fin-relay-1.0.4",
        "smartdesk-fin-relay-1.0.5",
    }
)

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
    WAKE = "WAKE"
    TARGET = "TARGET"
    MANUAL = "MANUAL"
    PANEL_RESET = "PANEL_RESET"


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
    awaiting_fresh_height: bool = False
    wake_observed_at: datetime | None = None
    wake_started_at: float | None = None
    fine_pulse_count: int = 0
    target_confirmation_count: int = 0
    target_confirmation_observed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class _StartupWakePending:
    """초기 live relay heartbeat를 기다리는, 아직 wire 명령이 없는 WAKE 의도."""

    deadline: float


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
        self._startup_wake_pending: _StartupWakePending | None = None
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
        self._last_stop_baseline_received_at: datetime | None = None

    async def start(self) -> None:
        """제어 runner를 시작하고 UP/DOWN 없이 안전 STOP 명령을 한 번 발행한다."""

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

        baseline = self._relay.get_snapshot().received_at
        error = await self._send_stop_bounded()
        if error is None:
            error = await self._wait_for_fresh_stop(baseline)
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
        if error is None:
            await self._register_startup_wake()
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
            self._cancel_startup_wake_pending_locked()
            if self._control.awaiting_fresh_height:
                if (
                    self._control.mode is _MotionMode.TARGET
                    and self._control.target_height_cm == target
                ):
                    return
                raise DeskCommandRejectedError("높이 센서를 깨우는 중에는 목표를 바꿀 수 없습니다.")
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

        height = self._height_monitor.get_snapshot()
        if height.status is not HeightStatus.ONLINE:
            basis = self._require_wake_basis(height)
            relay_snapshot = self._admit_wake()
            delta = target - basis.height_cm
            if abs(delta) <= self._settings.target_tolerance_cm:
                await self._set_sensor_check_needed(target)
                return
            direction = Direction.UP if delta > 0 else Direction.DOWN
            self._require_direction_allowed(basis.height_cm, direction)
            await self._begin_wake(
                mode=_MotionMode.TARGET,
                direction=direction,
                target=target,
                basis=basis,
                relay=relay_snapshot,
                detail=f"목표 {target:.1f}cm 전 센서 높이를 확인합니다.",
            )
            return

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
            self._cancel_startup_wake_pending_locked()
            target = self._control.target_height_cm
            if self._control.mode is not _MotionMode.TARGET or target is None:
                raise DeskCommandRejectedError("활성 목표가 없어 높이를 증감할 수 없습니다.")
        await self._set_target(target + amount if increase else target - amount)

    async def _hold(self, direction: Direction) -> None:
        if not self._running or self._closing:
            raise RuntimeError("책상 제어기가 실행 중이 아닙니다.")
        if self._stop_in_progress:
            raise DeskCommandRejectedError("STOP 명령이 진행 중입니다.")
        snapshot = self._height_monitor.get_snapshot()
        async with self._command_lock:
            self._cancel_startup_wake_pending_locked()
            if self._control.awaiting_fresh_height:
                if (
                    self._control.mode is _MotionMode.MANUAL
                    and self._control.direction is direction
                ):
                    self._last_hold_at = self._monotonic()
                    self._control = replace(
                        self._control,
                        detail=f"센서 깨우기 후 수동 {direction.value} 입력을 기다립니다.",
                        updated_at=self._require_utc(self._now()),
                    )
                    self._wake_event.set()
                    return
                raise DeskCommandRejectedError("높이 센서를 깨우는 중입니다.")

        if snapshot.status is not HeightStatus.ONLINE:
            basis = self._require_wake_basis(snapshot)
            relay_snapshot = self._admit_wake()
            self._require_direction_allowed(basis.height_cm, direction)
            await self._begin_wake(
                mode=_MotionMode.MANUAL,
                direction=direction,
                target=None,
                basis=basis,
                relay=relay_snapshot,
                detail=f"수동 {direction.value} 전 센서 높이를 확인합니다.",
            )
            return

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

    async def _register_startup_wake(self) -> None:
        """안전 STOP 명령 뒤 cache 기반 WAKE 후보를 runner에 제한 시간 동안 등록한다."""

        snapshot = self._height_monitor.get_snapshot()
        if snapshot.status is HeightStatus.ONLINE:
            return
        try:
            basis = self._require_wake_basis(snapshot)
            assert basis.height_cm is not None
            midpoint = (
                self._settings.operation_min_cm + self._settings.operation_max_cm
            ) / 2
            direction = Direction.UP if basis.height_cm < midpoint else Direction.DOWN
            self._require_direction_allowed(basis.height_cm, direction)
        except DeskCommandRejectedError as error:
            await self._set_startup_sensor_check_needed(str(error))
            return

        async with self._command_lock:
            if not self._running or self._closing or self._stop_in_progress:
                return
            self._startup_wake_pending = _StartupWakePending(
                deadline=self._monotonic() + self._settings.wake_timeout_seconds
            )
            self._control = replace(
                self._control,
                public_state=DeskState.IDLE,
                detail="시작 후 릴레이 상태를 기다려 높이 센서를 확인합니다.",
                last_error=None,
                updated_at=self._require_utc(self._now()),
            )

    async def _run_startup_wake_cycle(self) -> bool:
        """등록된 startup WAKE를 heartbeat 도착까지 poll/event 주기로 재평가한다."""

        async with self._intent_lock:
            pending = self._startup_wake_pending
            if pending is None:
                return False
            if not self._running or self._closing or self._stop_in_progress:
                self._startup_wake_pending = None
                return True

            snapshot = self._height_monitor.get_snapshot()
            if snapshot.status is HeightStatus.ONLINE:
                self._startup_wake_pending = None
                return True
            try:
                basis = self._require_wake_basis(snapshot)
            except DeskCommandRejectedError as error:
                await self._set_startup_sensor_check_needed(str(error))
                return True

            if self._monotonic() > pending.deadline:
                await self._set_startup_sensor_check_needed(
                    "시작 WAKE 대기 시간 안에 유효한 릴레이 live 상태를 받지 못했습니다."
                )
                return True

            try:
                relay = self._admit_wake()
            except DeskCommandRejectedError:
                # 구독 직후의 빈 snapshot 등 일시적인 미준비 상태는 다음 주기에 재시도한다.
                return True

            assert basis.height_cm is not None
            midpoint = (
                self._settings.operation_min_cm + self._settings.operation_max_cm
            ) / 2
            direction = Direction.UP if basis.height_cm < midpoint else Direction.DOWN
            try:
                self._require_direction_allowed(basis.height_cm, direction)
                self._startup_wake_pending = None
                await self._begin_wake(
                    mode=_MotionMode.WAKE,
                    direction=direction,
                    target=None,
                    basis=basis,
                    relay=relay,
                    detail="시작 후 높이 센서 연결을 확인하고 있습니다.",
                )
            except DeskCommandRejectedError as error:
                await self._set_startup_sensor_check_needed(str(error))
            return True

    async def _set_startup_sensor_check_needed(self, error: str) -> None:
        """startup WAKE 대기를 끝내도 앱 lifecycle은 IDLE/READY로 유지한다."""

        async with self._command_lock:
            self._startup_wake_pending = None
            if not self._running or self._closing:
                return
            self._control = replace(
                self._control,
                public_state=DeskState.IDLE,
                detail="높이 센서 확인이 필요합니다.",
                last_error=error,
                updated_at=self._require_utc(self._now()),
            )

    async def _set_sensor_check_needed(self, target: float) -> None:
        """stale cache가 목표 오차 안일 때 도달을 확정하거나 nudge하지 않는다."""

        async with self._command_lock:
            self._require_running_locked()
            self._control = _ControlState(
                public_state=DeskState.IDLE,
                mode=_MotionMode.NONE,
                target_phase=None,
                target_height_cm=None,
                direction=None,
                detail=(
                    f"목표 {target:.1f}cm가 마지막 높이와 가깝습니다. "
                    "도달 여부를 확인하려면 센서 높이가 필요합니다."
                ),
                last_error=None,
                updated_at=self._require_utc(self._now()),
                generation=self._control.generation + 1,
            )
            self._reset_motion_fields()

    async def _begin_wake(
        self,
        *,
        mode: _MotionMode,
        direction: Direction,
        target: float | None,
        basis: HeightSnapshot,
        relay: RelaySnapshot,
        detail: str,
    ) -> None:
        """동일 generation에서 WAKE 한 번만 발행하고 새 ONLINE 관측을 기다린다."""

        assert basis.height_cm is not None
        assert basis.observed_at is not None
        async with self._command_lock:
            self._require_running_locked()
            if self._control.awaiting_fresh_height:
                raise DeskCommandRejectedError("높이 센서를 이미 깨우는 중입니다.")
            generation = self._control.generation + 1
            started_at = self._monotonic()
            self._control = _ControlState(
                public_state=DeskState.WAKING,
                mode=mode,
                target_phase=_TargetPhase.CONTINUOUS if mode is _MotionMode.TARGET else None,
                target_height_cm=target,
                direction=direction,
                detail=detail,
                last_error=None,
                updated_at=self._require_utc(self._now()),
                generation=generation,
                awaiting_fresh_height=True,
                wake_observed_at=basis.observed_at,
                wake_started_at=started_at,
            )
            self._motion_started_at = started_at if mode is not _MotionMode.WAKE else None
            self._last_hold_at = started_at if mode is _MotionMode.MANUAL else None
            self._next_refresh_at = None
            self._settle_until = None
            self._last_fine_observed_at = None
            self._initialize_relay_tracking(relay)

        wake_error: str | None = None
        async with self._relay_io_lock:
            try:
                async with asyncio.timeout(self._settings.relay_ack_timeout_seconds):
                    await self._relay.wake(direction, basis.height_cm)
            except Exception as error:
                wake_error = str(error)
        if wake_error is not None:
            async with self._command_lock:
                if self._control.generation != generation:
                    return
            await self._stop_motion(
                "높이 센서 깨우기 명령에 실패했습니다.",
                error_detail=wake_error,
            )
            return
        self._wake_event.set()

    async def _run_wake_cycle(self, control: _ControlState) -> None:
        """WAKE 이후에는 새 observed_at·relay ready까지 어떤 UP/DOWN도 보내지 않는다."""

        now_mono = self._monotonic()
        if (
            control.wake_started_at is None
            or now_mono - control.wake_started_at
            > self._settings.wake_timeout_seconds
        ):
            raise DeskCommandRejectedError("센서 깨우기 뒤 새 높이 관측 시간이 초과되었습니다.")
        if (
            control.mode is _MotionMode.MANUAL
            and self._last_hold_at is not None
            and now_mono - self._last_hold_at > self._settings.manual_watchdog_seconds
        ):
            await self._stop_motion(
                "수동 HOLD 입력이 끊겨 센서 깨우기를 취소하고 정지했습니다.",
                error_detail=None,
            )
            return

        snapshot = self._height_monitor.get_snapshot()
        if snapshot.status is HeightStatus.ERROR:
            raise DeskCommandRejectedError("높이 센서 transport가 오류 상태입니다.")
        if (
            snapshot.status is not HeightStatus.ONLINE
            or snapshot.observed_at is None
            or control.wake_observed_at is None
            or snapshot.observed_at <= control.wake_observed_at
        ):
            return

        relay = self._require_relay_available_for_wake()
        expected = RelayState(control.direction.value) if control.direction is not None else None
        # WAKE 중 새 height lease가 준비되면 firmware는 아직 400ms deadline이 남아
        # 있어도 UP/DOWN + ready를 발행한다. 같은 방향이면 즉시 일반 pulse로
        # deadline을 연장한다. STOP만 기다리면 timeout event(code=timeout) 뒤 다음
        # 5초 heartbeat까지 불필요하게 대기하게 된다.
        allowed_states = {RelayState.STOP}
        if control.mode is not _MotionMode.WAKE:
            allowed_states.add(expected)
        if relay.code != "ready" or relay.state not in allowed_states:
            return
        assert snapshot.height_cm is not None
        await self._activate_after_wake(control, snapshot, relay, now_mono)

    async def _activate_after_wake(
        self,
        control: _ControlState,
        height: HeightSnapshot,
        relay: RelaySnapshot,
        now_mono: float,
    ) -> None:
        assert height.height_cm is not None
        if control.mode is _MotionMode.WAKE:
            async with self._command_lock:
                if self._control.generation != control.generation:
                    return
                self._control = _ControlState(
                    public_state=DeskState.IDLE,
                    mode=_MotionMode.NONE,
                    target_phase=None,
                    target_height_cm=None,
                    direction=None,
                    detail="높이 센서 연결을 확인했습니다.",
                    last_error=None,
                    updated_at=self._require_utc(self._now()),
                    generation=control.generation + 1,
                )
                self._reset_motion_fields()
            return
        if control.mode is _MotionMode.MANUAL:
            assert control.direction is not None
            self._require_direction_allowed(height.height_cm, control.direction)
            async with self._command_lock:
                if self._control.generation != control.generation:
                    return
                self._control = replace(
                    self._control,
                    public_state=DeskState.MANUAL,
                    detail=f"수동 {control.direction.value} 이동을 시작합니다.",
                    awaiting_fresh_height=False,
                    wake_observed_at=None,
                    wake_started_at=None,
                    updated_at=self._require_utc(self._now()),
                )
                self._next_refresh_at = now_mono
                self._initialize_relay_tracking(relay)
            return

        assert control.mode is _MotionMode.TARGET
        assert control.target_height_cm is not None
        delta = control.target_height_cm - height.height_cm
        if abs(delta) <= self._settings.target_tolerance_cm:
            await self._stop_motion(
                f"목표 {control.target_height_cm:.1f}cm에 도달했습니다.",
                error_detail=None,
            )
            return
        direction = Direction.UP if delta > 0 else Direction.DOWN
        self._require_direction_allowed(height.height_cm, direction)
        async with self._command_lock:
            if self._control.generation != control.generation:
                return
            self._control = replace(
                self._control,
                public_state=DeskState.MOVING,
                target_phase=_TargetPhase.CONTINUOUS,
                direction=direction,
                detail=f"목표 {control.target_height_cm:.1f}cm로 이동합니다.",
                awaiting_fresh_height=False,
                wake_observed_at=None,
                wake_started_at=None,
                updated_at=self._require_utc(self._now()),
            )
            self._next_refresh_at = now_mono
            self._initialize_relay_tracking(relay)

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

        if self._height_monitor.panel_reset_active():
            await self._run_panel_reset_cycle()
            return
        if control.mode is _MotionMode.PANEL_RESET:
            await self._stop_motion("패널 초기화가 완료되어 릴레이를 정지했습니다.", error_detail=None)
            return

        if control.mode is _MotionMode.NONE:
            if await self._run_startup_wake_cycle():
                return
            await self._watch_inactive_relay()
            return

        try:
            if control.awaiting_fresh_height:
                await self._run_wake_cycle(control)
                return
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

    async def _run_panel_reset_cycle(self) -> None:
        """Hold DOWN only while the Arduino observes the non-numeric rSt panel screen."""

        async with self._command_lock:
            control = self._control
            if control.mode is not _MotionMode.PANEL_RESET:
                self._invalidate_motion_locked("PANEL_RESET")
                control = replace(
                    self._control,
                    public_state=DeskState.WAKING,
                    mode=_MotionMode.PANEL_RESET,
                    target_phase=None,
                    target_height_cm=None,
                    direction=Direction.DOWN,
                    detail="패널 rSt 초기화를 위해 DOWN을 유지하고 있습니다.",
                    last_error=None,
                    generation=self._control.generation + 1,
                    updated_at=self._require_utc(self._now()),
                )
                self._control = control
                self._next_refresh_at = self._monotonic()
            if self._next_refresh_at is not None and self._monotonic() < self._next_refresh_at:
                return

        # rSt has no trustworthy numeric height.  The firmware still bounds
        # every command to 400ms; QoS 0 ensures missed refreshes fail closed
        # instead of accumulating stale DOWN commands at the broker.
        basis = max(self._settings.operation_min_cm + 0.1, 75.1)
        try:
            async with self._relay_io_lock:
                await self._relay.wake(Direction.DOWN, basis)
        except Exception as error:
            await self._stop_motion("패널 초기화 DOWN 명령 발행에 실패했습니다.", error_detail=str(error))
            return
        async with self._command_lock:
            if self._control.generation == control.generation:
                self._next_refresh_at = self._monotonic() + self._settings.pulse_refresh_interval_seconds

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
            # 7-segment scanner가 이동 중 한 번 잘못 읽은, 그러나 범위상
            # 유효한 숫자만으로 목표 도달을 확정하면 실제 책상은 중간에서
            # 멈출 수 있다. 서로 다른 최신 관측 두 번을 요구한다.
            if height.observed_at == control.target_confirmation_observed_at:
                return
            confirmations = control.target_confirmation_count + 1
            if confirmations < 2:
                async with self._command_lock:
                    if self._control.generation != control.generation:
                        return
                    self._control = replace(
                        self._control,
                        target_confirmation_count=confirmations,
                        target_confirmation_observed_at=height.observed_at,
                        detail="목표 높이를 한 번 더 확인하고 있습니다.",
                        updated_at=self._require_utc(self._now()),
                    )
                return
            await self._stop_motion(
                f"목표 {control.target_height_cm:.1f}cm에 도달했습니다.",
                error_detail=None,
            )
            return

        if control.target_confirmation_count:
            async with self._command_lock:
                if self._control.generation == control.generation:
                    self._control = replace(
                        self._control,
                        target_confirmation_count=0,
                        target_confirmation_observed_at=None,
                        detail=f"목표 {control.target_height_cm:.1f}cm로 이동합니다.",
                        updated_at=self._require_utc(self._now()),
                    )
                    control = self._control

        needed = Direction.UP if delta > 0 else Direction.DOWN
        self._require_direction_allowed(height.height_cm, needed)
        if control.target_phase is _TargetPhase.SETTLING:
            if self._settle_until is not None and now_mono < self._settle_until:
                return
            if control.fine_pulse_count >= self._settings.max_fine_pulses:
                await self._stop_motion(
                    "미세 보정 횟수 제한에 도달해 현재 높이에서 정지했습니다.",
                    error_detail=None,
                )
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
                    fine_pulse_count=self._control.fine_pulse_count + 1,
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
        if self._stop_in_progress:
            return
        snapshot = self._relay.get_snapshot()
        if snapshot.state not in {RelayState.UP, RelayState.DOWN}:
            return
        if snapshot.received_at is None:
            return
        if (
            self._last_stop_baseline_received_at is not None
            and snapshot.received_at <= self._last_stop_baseline_received_at
        ):
            return
        if snapshot.received_at == self._last_unexpected_relay_at:
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
        stop_task = asyncio.create_task(
            self._stop_and_confirm(
                detail,
                error_detail=error_detail,
                reject_if_stopping=False,
            )
        )
        try:
            stop_error = await asyncio.shield(stop_task)
        except asyncio.CancelledError:
            await asyncio.gather(stop_task, return_exceptions=True)
            raise
        if stop_error is not None and error_detail is None:
            raise RuntimeError(stop_error)

    async def _stop_for_transition(self, detail: str) -> None:
        error = await self._stop_and_confirm(
            detail,
            error_detail=None,
            reject_if_stopping=True,
        )
        if error is not None:
            raise DeskCommandRejectedError(error)

    async def _stop_and_confirm(
        self,
        detail: str,
        *,
        error_detail: str | None,
        reject_if_stopping: bool,
    ) -> str | None:
        """이동 의도를 무효화하고 baseline 이후 live STOP까지 확인한다."""

        async with self._command_lock:
            if reject_if_stopping:
                self._require_running_locked()
            elif not self._running and not self._closing:
                raise RuntimeError("책상 제어기가 실행 중이 아닙니다.")
            if self._stop_in_progress:
                if reject_if_stopping:
                    raise DeskCommandRejectedError("다른 STOP 명령이 진행 중입니다.")
                return
            baseline = self._relay.get_snapshot().received_at
            self._stop_in_progress = True
            self._invalidate_motion_locked(detail)
            self._last_stop_baseline_received_at = baseline
        self._wake_event.set()
        stop_error = await self._send_stop_bounded()
        if stop_error is None:
            stop_error = await self._wait_for_fresh_stop(baseline)
        final_error = error_detail
        if stop_error is not None:
            final_error = (
                f"{error_detail}; STOP 실패: {stop_error}"
                if error_detail
                else stop_error
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
        return stop_error

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

    def _admit_wake(self) -> RelaySnapshot:
        """정상 이동과 달리 stale/cache 기준 WAKE만 별도로 허용한다."""

        if not self._running or self._closing:
            raise RuntimeError("책상 제어기가 실행 중이 아닙니다.")
        if self._stop_in_progress:
            raise DeskCommandRejectedError("STOP 명령이 진행 중입니다.")
        relay = self._require_relay_available_for_wake()
        if relay.state is not RelayState.STOP:
            raise DeskCommandRejectedError("WAKE는 릴레이 STOP 상태에서만 허용됩니다.")
        return relay

    def _require_wake_basis(self, snapshot: HeightSnapshot) -> HeightSnapshot:
        """stale/cache 값은 방향과 경계만 위한 WAKE 근거로 제한한다."""

        if (
            snapshot.status not in {HeightStatus.STALE, HeightStatus.SENSOR_SLEEPING}
            or snapshot.height_cm is None
            or snapshot.observed_at is None
            or not math.isfinite(snapshot.height_cm)
            or not self._settings.measurement_min_cm
            <= snapshot.height_cm
            <= self._settings.measurement_max_cm
        ):
            raise DeskCommandRejectedError("WAKE를 위한 유효한 마지막 높이가 없습니다.")
        return snapshot

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
        snapshot = self._require_relay_available_for_wake()
        if snapshot.code in {"height_waiting", "height_not_ready", "height_stale"}:
            raise DeskCommandRejectedError("펌웨어의 높이 안전 lease가 준비되지 않았습니다.")
        return snapshot

    def _require_relay_available_for_wake(self) -> RelaySnapshot:
        snapshot = self._relay.get_snapshot()
        if snapshot.last_error is not None:
            raise DeskCommandRejectedError("릴레이 상태 payload가 유효하지 않습니다.")
        if snapshot.event is None:
            raise DeskCommandRejectedError("릴레이 live 상태를 아직 받지 못했습니다.")
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
        self._cancel_startup_wake_pending_locked()
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

    def _cancel_startup_wake_pending_locked(self) -> None:
        """사용자·종료 의도 뒤 startup WAKE가 늦게 발행되지 않게 한다."""

        self._startup_wake_pending = None

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
