"""ESP32 없이 라즈베리파이 GPIO로 틸트를 구동하는 link.

`TiltMqttLink`/`TiltSerialLink`와 같은 인터페이스를 제공하므로 `TiltController`는
바뀐 줄 모른다. 링크 뒤에서 ESP32 `tilt_protocol.cpp`가 하던 명령 해석·이벤트
발행·개루프 위치 추정을 수행한다.

보정 속도는 서버가 연결 시 CALIBRATE로 밀어 넣는다. 기존 실측값이 그대로
승계되므로 여기에 속도를 하드코딩하지 않는다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math

from smart_desk.config.settings import TiltSettings
from smart_desk.modules.tilt.gpio_motion import (
    MAX_DUTY_PERCENT,
    MIN_DUTY_PERCENT,
    GpioTiltMotion,
)
from smart_desk.modules.tilt.gpio_policy import (
    ABSOLUTE_MAX_MOTION_MS,
    TiltDirection,
    make_motion_plan,
    position_allowed,
)
from smart_desk.modules.tilt.serial_link import TiltLinkSnapshot, TiltLinkStatus


LOGGER = logging.getLogger(__name__)

FIRMWARE = "rpi-gpio-tilt-1.0.0"
# ESP32 STATUS_HEARTBEAT_MS와 같은 주기. 서버가 이걸 동기화 입력으로 쓴다.
HEARTBEAT_SECONDS = 5.0
MIN_RUN_DURATION_MS = 50


class TiltGpioLink:
    """GPIO 구동을 라인 프로토콜 뒤에 감춘다."""

    def __init__(self, settings: TiltSettings) -> None:
        self._settings = settings
        self._inbox: asyncio.Queue[bytes] = asyncio.Queue()
        self._started = False
        self._last_error: str | None = None
        self._motion: GpioTiltMotion | None = None
        self._heartbeat: asyncio.Task[None] | None = None

        # 개루프 위치 추정 상태. ESP32와 같은 규칙을 지켜야 서버 보정이 맞는다.
        self._position_mm = 0.0
        self._position_valid = False
        self._moving_target_mm = 0.0
        self._manual_run = False
        # speeds[duty][UP|DOWN] = mm/s
        self._speeds: dict[int, dict[TiltDirection, float]] = {}

    @property
    def connection_generation(self) -> int:
        """물리 재연결이 없으므로 세대는 고정이다."""

        return 1

    async def start(self) -> None:
        if self._started:
            return
        self._motion = GpioTiltMotion(
            self._settings.gpio_r_en_pin,
            self._settings.gpio_l_en_pin,
            self._settings.gpio_r_pwm_pin,
            self._settings.gpio_l_pwm_pin,
            self._settings.gpio_pwm_frequency_hz,
            on_timeout=self._on_motion_timeout,
        )
        self._started = True
        self._last_error = None
        self._emit_status("ready")
        self._heartbeat = asyncio.create_task(self._heartbeat_loop())

    async def stop(self) -> None:
        if not self._started:
            return
        self._started = False
        task, self._heartbeat = self._heartbeat, None
        if task is not None and not task.done():
            task.cancel()
        if self._motion is not None:
            self._motion.close()
            self._motion = None

    async def write_line(self, command: str) -> bool:
        if not self._started:
            raise RuntimeError("GPIO link를 시작한 뒤 써야 합니다.")
        try:
            self._handle_line(command.strip())
        except Exception as error:  # noqa: BLE001 - 링크 실패로 보고한다.
            self._last_error = str(error)
            LOGGER.warning(
                "틸팅 GPIO 명령을 처리하지 못했습니다.",
                exc_info=True,
                extra={"component": "tilt_gpio", "event": "tilt_gpio_command_failed"},
            )
            return False
        self._last_error = None
        return True

    async def write_line_if_connected(self, command: str) -> bool:
        if not self._started:
            return False
        return await self.write_line(command)

    async def read_line(self, timeout_seconds: float | None = None) -> bytes:
        if not self._started:
            raise RuntimeError("GPIO link를 시작한 뒤 읽어야 합니다.")
        timeout = timeout_seconds if timeout_seconds and timeout_seconds > 0 else 1.0
        try:
            return await asyncio.wait_for(self._inbox.get(), timeout=timeout)
        except TimeoutError:
            return b""

    def get_snapshot(self) -> TiltLinkSnapshot:
        if not self._started:
            return TiltLinkSnapshot(TiltLinkStatus.STOPPED, self._last_error)
        if self._last_error is not None:
            return TiltLinkSnapshot(TiltLinkStatus.ERROR, self._last_error)
        # 물리 링크가 없으므로 침묵으로 끊김을 판정하지 않는다.
        return TiltLinkSnapshot(TiltLinkStatus.CONNECTED, None)

    # --- 명령 해석 (tilt_protocol.cpp::handle_line) ---

    def _handle_line(self, line: str) -> None:
        parts = line.split()
        if not parts:
            self._stop_and_invalidate_if_moving()
            self._emit_rejected("empty_command")
            return

        command, args = parts[0], parts[1:]
        if command == "STOP":
            if args:
                self._stop_and_invalidate_if_moving()
                self._emit_rejected("stop_arguments")
                return
            self._stop_and_invalidate_if_moving()
            self._emit_stopped("command")
            return
        if command == "STATUS":
            if args:
                self._stop_and_invalidate_if_moving()
                self._emit_rejected("status_arguments")
                return
            self._emit_status("status")
            return
        if command == "SET_POSITION":
            self._handle_set_position(args)
            return
        if command == "CALIBRATE":
            self._handle_calibrate(args)
            return
        if command == "MOVE_TO":
            self._handle_move_to(args)
            return
        if command == "RUN":
            self._handle_run(args)
            return
        self._stop_and_invalidate_if_moving()
        self._emit_rejected("unknown_command")

    def _handle_set_position(self, args: list[str]) -> None:
        if self._is_moving():
            self._stop_and_invalidate_if_moving()
            self._emit_rejected("busy")
            return
        position = _parse_float(args[0]) if len(args) == 1 else None
        if position is None or not position_allowed(position):
            self._stop_and_invalidate_if_moving()
            self._emit_rejected("invalid_position")
            return
        self._position_mm = position
        self._position_valid = True
        self._emit_status("ready")

    def _handle_calibrate(self, args: list[str]) -> None:
        if len(args) != 3 or self._is_moving():
            self._stop_and_invalidate_if_moving()
            self._emit_rejected("invalid_calibration")
            return
        duty = _parse_int(args[0])
        speed = _parse_float(args[1])
        direction = args[2]
        if (
            duty is None
            or speed is None
            or not self._set_calibration(duty, speed, direction)
        ):
            self._stop_and_invalidate_if_moving()
            self._emit_rejected("invalid_calibration")
            return
        self._emit_calibrated(duty, direction)

    def _handle_move_to(self, args: list[str]) -> None:
        if self._is_moving():
            self._stop_and_invalidate_if_moving()
            self._emit_rejected("busy")
            return
        if len(args) != 2:
            self._stop_and_invalidate_if_moving()
            self._emit_rejected("invalid_move_to")
            return
        target = _parse_float(args[0])
        duty = _parse_int(args[1])
        if (
            target is None
            or duty is None
            or duty < MIN_DUTY_PERCENT
            or duty > MAX_DUTY_PERCENT
        ):
            self._stop_and_invalidate_if_moving()
            self._emit_rejected("invalid_move_to")
            return

        plan = make_motion_plan(
            self._position_mm,
            self._position_valid,
            target,
            self._speed_for(duty, TiltDirection.UP),
            self._speed_for(duty, TiltDirection.DOWN),
        )
        if plan.at_target:
            self._emit_at_target()
            return
        assert self._motion is not None
        if plan.direction is TiltDirection.STOP or not self._motion.start(
            plan.direction, plan.duration_ms, duty
        ):
            self._stop_and_invalidate_if_moving()
            self._emit_rejected("move_not_armed")
            return
        self._moving_target_mm = target
        self._manual_run = False
        self._emit(
            {
                "event": "moving",
                "target_mm": round(target, 2),
                "direction": plan.direction.value,
                "position_valid": True,
                "position_mm": round(self._position_mm, 2),
            }
        )

    def _handle_run(self, args: list[str]) -> None:
        if self._is_moving():
            self._stop_and_invalidate_if_moving()
            self._emit_rejected("busy")
            return
        if len(args) != 3:
            self._stop_and_invalidate_if_moving()
            self._emit_rejected("invalid_run")
            return
        direction = (
            TiltDirection.UP
            if args[0] == "UP"
            else TiltDirection.DOWN
            if args[0] == "DOWN"
            else TiltDirection.STOP
        )
        duty = _parse_int(args[1])
        duration_ms = _parse_int(args[2])
        if (
            direction is TiltDirection.STOP
            or duty is None
            or duration_ms is None
            or duty < MIN_DUTY_PERCENT
            or duty > MAX_DUTY_PERCENT
            or duration_ms < MIN_RUN_DURATION_MS
            or duration_ms > ABSOLUTE_MAX_MOTION_MS
        ):
            self._stop_and_invalidate_if_moving()
            self._emit_rejected("invalid_run")
            return
        assert self._motion is not None
        if not self._motion.start(direction, duration_ms, duty):
            self._stop_and_invalidate_if_moving()
            self._emit_rejected("run_not_armed")
            return
        # 시간 기반 수동 이동은 위치 센서가 없으므로 절대 위치를 주장하지 않는다.
        self._position_valid = False
        self._manual_run = True
        self._emit(
            {
                "event": "moving",
                "direction": direction.value,
                "duty": duty,
                "duration_ms": duration_ms,
                "position_valid": False,
            }
        )

    # --- 상태 전이 ---

    def _on_motion_timeout(self) -> None:
        """duration 만료. ESP32 handle_timer_event와 같은 규칙이다."""

        if self._manual_run:
            self._manual_run = False
            self._position_valid = False
            self._emit_stopped("manual_complete")
            return
        self._position_mm = self._moving_target_mm
        self._position_valid = True
        self._emit_at_target()

    def _stop_and_invalidate_if_moving(self) -> None:
        if self._motion is not None and self._motion.stop():
            # 중도 정지는 이동 거리를 알 수 없으므로 위치를 버린다.
            self._position_valid = False
        self._manual_run = False

    def _is_moving(self) -> bool:
        return self._motion is not None and self._motion.is_moving()

    def _set_calibration(self, duty: int, speed_mm_s: float, direction: str) -> bool:
        if (
            duty < MIN_DUTY_PERCENT
            or duty > MAX_DUTY_PERCENT
            or not math.isfinite(speed_mm_s)
            or speed_mm_s <= 0
            or direction not in {"UP", "DOWN"}
        ):
            return False
        entry = self._speeds.setdefault(duty, {})
        entry[TiltDirection(direction)] = speed_mm_s
        return True

    def _speed_for(self, duty: int, direction: TiltDirection) -> float:
        return self._speeds.get(duty, {}).get(direction, 0.0)

    # --- 이벤트 발행 ---

    async def _heartbeat_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(HEARTBEAT_SECONDS)
            except asyncio.CancelledError:
                raise
            self._emit_status("status")

    def _emit_status(self, event: str) -> None:
        self._emit({"event": event})

    def _emit_rejected(self, reason: str) -> None:
        self._emit({"event": "rejected", "reason": reason})

    def _emit_stopped(self, reason: str) -> None:
        self._emit({"event": "stopped", "reason": reason})

    def _emit_at_target(self) -> None:
        self._emit({"event": "at_target"})

    def _emit_calibrated(self, duty: int, direction: str) -> None:
        self._emit({"event": "calibrated", "duty": duty, "direction": direction})

    def _emit(self, payload: dict[str, object]) -> None:
        """firmware/위치 필드를 채워 한 줄로 큐에 넣는다.

        position_valid가 False면 position_mm 키를 넣지 않는다(ESP32와 동일).
        """

        body: dict[str, object] = {"firmware": FIRMWARE}
        body.update(payload)
        body.setdefault("position_valid", self._position_valid)
        if body["position_valid"] and "position_mm" not in body:
            body["position_mm"] = round(self._position_mm, 2)
        if not body["position_valid"]:
            body.pop("position_mm", None)
        self._inbox.put_nowait(json.dumps(body, ensure_ascii=False).encode())


def _parse_int(token: str) -> int | None:
    try:
        return int(token)
    except (TypeError, ValueError):
        return None


def _parse_float(token: str) -> float | None:
    try:
        value = float(token)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None
