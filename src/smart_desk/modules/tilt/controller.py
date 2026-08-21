"""틸팅 MQTT 명령을 안전한 ESP32 시리얼 제어로 변환한다."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
import json
import logging
import math

from pydantic import ValidationError

from smart_desk.config.settings import TiltSettings
from smart_desk.core.task_manager import TaskManager
from smart_desk.modules.mqtt.client import MqttClient, MqttUnavailableError
from smart_desk.modules.mqtt.models import MqttMessage
from smart_desk.modules.mqtt.topics import TILT_STATUS_TOPIC
from smart_desk.modules.tilt.level_repository import TiltLevelRepository
from smart_desk.modules.tilt.messages import (
    TiltCommandAdapter,
    TiltGotoCommand,
    TiltStatusMessage,
    TiltStopCommand,
)
from smart_desk.modules.tilt.models import TiltSnapshot, TiltState
from smart_desk.modules.tilt.serial_link import TiltSerialLink


LOGGER = logging.getLogger(__name__)
TILT_READER_TASK_NAME = "tilt-serial-reader"
TILT_PREPARE_TASK_NAME = "tilt-prepare"
TILT_MOTION_TASK_NAME = "tilt-motion"
TILT_AUTO_HOME_TASK_NAME = "tilt-auto-home"
# 0단계는 위치를 믿지 않고 바닥까지 내려 영점을 다시 잡는다. 여유 2초는
# 전체 행정을 다 내려간 뒤 실제 바닥에 닿게 하는 몫이다.
HOMING_MARGIN_MS = 2000
# 펌웨어 TiltPolicy::ABSOLUTE_MAX_MOTION_MS와 같은 상한이다.
HOMING_MAX_DURATION_MS = 16000


class TiltCommandRejectedError(RuntimeError):
    """현재 장치 상태에서 틸트 명령을 실행할 수 없다."""


def utc_now() -> datetime:
    """현재 timezone-aware UTC 시각을 반환한다."""

    return datetime.now(UTC)


class TiltController:
    """틸팅 ESP32의 단일 서버측 소유자다.

    장치의 GPIO·motion deadline은 firmware가 소유한다. 이 controller는 명령 검증,
    연결 세대별 보정, 상태 전이와 STOP 우선순위만 맡는다.
    """

    def __init__(
        self,
        link: TiltSerialLink,
        levels: TiltLevelRepository,
        mqtt: MqttClient,
        settings: TiltSettings,
        task_manager: TaskManager,
    ) -> None:
        self._link = link
        self._levels = levels
        self._mqtt = mqtt
        self._settings = settings
        self._task_manager = task_manager
        self._snapshot = TiltSnapshot(
            state=TiltState.ERROR,
            level=None,
            target_level=None,
            position_mm=None,
            position_valid=False,
            firmware=None,
            detail="틸팅 제어기가 시작되지 않았습니다.",
            last_error="틸팅 제어기가 시작되지 않았습니다.",
            updated_at=utc_now(),
        )
        self._running = False
        self._state_lock = asyncio.Lock()
        self._reader_task: asyncio.Task[None] | None = None
        self._prepare_task: asyncio.Task[None] | None = None
        self._motion_task: asyncio.Task[None] | None = None
        self._synced_generation = -1
        self._command_generation = 0
        self._homing_generation: int | None = None
        # 연결 세대마다 자동 영점 복귀는 한 번만 시도한다.
        self._auto_home_generation: int | None = None
        self._pending_calibration: tuple[int, str, int, asyncio.Future[None]] | None = None

    async def start(self) -> None:
        """시리얼 reader를 시작하고 firmware ready 이벤트를 기다린다."""

        if self._running:
            raise RuntimeError("틸팅 제어기가 이미 실행 중입니다.")
        await self._link.start()
        self._running = True
        self._synced_generation = -1
        self._auto_home_generation = None
        await self._replace_snapshot(
            state=TiltState.ERROR,
            level=None,
            target_level=None,
            position_mm=None,
            position_valid=False,
            detail="틸팅 ESP32의 준비 상태를 기다립니다.",
            last_error="틸팅 ESP32가 아직 준비되지 않았습니다.",
        )
        try:
            self._reader_task = self._task_manager.create(
                TILT_READER_TASK_NAME,
                self._run_reader(),
                critical=False,
            )
        except Exception:
            self._running = False
            await self._link.stop()
            raise

    async def stop(self) -> None:
        """새 이동을 막고 연결된 장치에 STOP을 보낸 뒤 포트를 닫는다."""

        self._running = False
        async with self._state_lock:
            self._command_generation += 1
            pending = self._pending_calibration
            self._pending_calibration = None
            if pending is not None and not pending[3].done():
                pending[3].cancel()

        # 새 연결을 만들지 않는 종료 STOP이다. 연결이 없으면 firmware deadline이 최후
        # 보호 계층이다.
        sent = await self._link.write_line_if_connected("STOP")
        await self._cancel_tasks(self._motion_task, self._prepare_task, self._reader_task)
        self._motion_task = None
        self._prepare_task = None
        self._reader_task = None
        await self._link.stop()
        if sent:
            await self._replace_snapshot(
                state=TiltState.STOPPED,
                target_level=None,
                detail="애플리케이션 종료 전에 STOP을 전송했습니다.",
                last_error=None,
            )
        else:
            await self._replace_snapshot(
                state=TiltState.ERROR,
                target_level=None,
                detail="종료 STOP을 전송하지 못했습니다.",
                last_error="틸팅 ESP32 연결이 없어 종료 STOP을 확인하지 못했습니다.",
            )

    async def handle_command(self, message: MqttMessage) -> None:
        """MQTT GOTO/STOP을 처리한다. GOTO의 긴 I/O는 background task로 분리한다."""

        try:
            command = TiltCommandAdapter.validate_json(message.payload)
        except ValidationError as error:
            await self._publish_notice(f"유효하지 않은 틸팅 명령: {self._summarize_validation_error(error)}")
            return

        if isinstance(command, TiltStopCommand):
            await self._stop_motion("MQTT STOP 명령을 받았습니다.")
            return
        if message.retained:
            await self._publish_notice("retained GOTO 명령은 실행하지 않습니다.")
            return
        await self._start_goto(command)

    async def set_target(self, level: int) -> None:
        """HTTP 같은 프로세스 내부 호출을 현재 틸트 명령 경로로 보낸다."""

        detail = await self._start_goto(TiltGotoCommand(level=level, source="api"))
        if detail:
            raise TiltCommandRejectedError(detail)

    async def stop_motion(self, reason: str = "") -> None:
        """수명주기 종료와 구분되는 명시적 물리 STOP이다."""

        await self._stop_motion(reason or "틸팅 정지 요청을 받았습니다.")

    def get_snapshot(self) -> TiltSnapshot:
        """I/O 없이 마지막 틸트 상태를 반환한다."""

        return self._snapshot

    async def _start_goto(self, command: TiltGotoCommand) -> str | None:
        async with self._state_lock:
            snapshot = self._snapshot
            if not self._running:
                detail = "틸팅 제어기가 실행 중이 아닙니다."
            elif snapshot.state is TiltState.MOVING:
                if snapshot.target_level == command.level:
                    detail = None
                else:
                    detail = "다른 틸트 이동이 진행 중입니다."
            elif snapshot.state not in self._states_allowing(command.level):
                detail = "틸팅 ESP32가 준비되지 않았거나 오류 상태입니다."
            elif not snapshot.position_valid and command.level != self._settings.min_level:
                # 0단계는 바닥까지 내려 영점을 새로 잡으므로 현재 위치를 몰라도 된다.
                # 오히려 위치를 잃었을 때 복구하는 유일한 경로다.
                detail = "틸팅 ESP32의 현재 위치가 확인되지 않았습니다."
            elif not self._settings.min_level <= command.level <= self._settings.max_level:
                detail = f"단계는 {self._settings.min_level}~{self._settings.max_level} 사이여야 합니다."
            elif self._levels.target_mm_for_level(command.level) is None:
                detail = f"{command.level}단계의 목표 위치가 설정되지 않았습니다."
            elif (self._link.connection_generation != self._synced_generation
                    and command.level != self._settings.min_level):
                # 0단계는 시간 기반 RUN으로 내려가므로 firmware 보정이 없어도 된다.
                detail = "틸팅 ESP32 보정이 아직 완료되지 않았습니다."
            else:
                detail = ""

            if detail is None:
                # QoS 1 duplicate: 진행 중인 동일 목표는 오류로 바꾸지 않는다.
                pass
            elif detail:
                pass
            else:
                self._command_generation += 1
                generation = self._command_generation
                self._set_snapshot_locked(
                    replace(
                        snapshot,
                        state=TiltState.MOVING,
                        target_level=command.level,
                        detail=f"{command.level}단계 이동 명령을 전송합니다.",
                        last_error=None,
                    )
                )
                self._motion_task = self._task_manager.create(
                    TILT_MOTION_TASK_NAME,
                    self._send_move(command.level, generation),
                    critical=False,
                )

        # 틸트 명령 경로는 그동안 아무 것도 남기지 않아, 대시보드에서 거절을
        # 받아도 서버 로그만으로는 어떤 상태가 원인이었는지 알 수 없었다.
        # 수락/거절과 그 판단에 쓰인 상태를 함께 남긴다.
        LOGGER.info(
            "틸팅 단계 이동 요청을 처리했습니다.",
            extra={
                "component": "tilt",
                "event": "tilt_goto_rejected" if detail else "tilt_goto_accepted",
                "detail": detail or "",
                "requested_level": command.level,
                "source": command.source,
                "state": snapshot.state.value,
                "level": snapshot.level,
                "target_level": snapshot.target_level,
                "position_valid": snapshot.position_valid,
                "position_mm": snapshot.position_mm,
                "link_generation": self._link.connection_generation,
                "synced_generation": self._synced_generation,
            },
        )

        if detail is None:
            await self._publish_status()
        elif detail:
            await self._publish_notice(detail)
        else:
            await self._publish_status()
        return detail

    def _states_allowing(self, level: int) -> set[TiltState]:
        """그 단계를 시작할 수 있는 상태들.

        0단계는 위치를 몰라 ERROR로 남은 상태에서도 받아 준다. 부팅 직후처럼
        위치만 모르는 경우가 바로 영점을 다시 잡아야 하는 상황이고, 이때 막으면
        장치를 되살릴 방법이 없다. 장치 고장(last_error가 붙은 정지·시간 초과)은
        _send_home 직전 write 실패로 여전히 걸러진다.
        """

        ready = {TiltState.IDLE, TiltState.AT_TARGET, TiltState.STOPPED}
        return ready | {TiltState.ERROR} if level == self._settings.min_level else ready

    def _homing_duration_ms(self) -> int:
        """바닥까지 확실히 내려가는 하강 시간. 현재 위치를 믿지 않는다.

        위치는 센서 없이 추측항법으로만 관리해 오차가 쌓인다. 그래서 0단계는
        어디에 있든 전체 행정을 내려간 뒤 잠깐 더 밀어 실제 바닥에 닿게 하고,
        그 지점을 새 영점으로 삼는다.
        """

        duty = self._settings.move_duty_percent
        speed = self._levels.down_speed_mm_s(duty)
        travel_mm = self._levels.max_target_mm()
        if not speed or speed <= 0 or travel_mm <= 0:
            # 보정이 없으면 펌웨어가 허용하는 최대치까지 내려간다.
            return HOMING_MAX_DURATION_MS
        full_travel_ms = int((travel_mm / speed) * 1000)
        return min(full_travel_ms + HOMING_MARGIN_MS, HOMING_MAX_DURATION_MS)

    async def _send_home(self, command_generation: int) -> None:
        """0단계: 바닥까지 내린 뒤 그 자리를 0으로 다시 선언한다."""

        duration_ms = self._homing_duration_ms()
        duty = self._settings.move_duty_percent
        self._homing_generation = command_generation
        if not await self._is_current_motion(command_generation):
            return
        if not await self._link.write_line(f"RUN DOWN {duty} {duration_ms}"):
            await self._mark_motion_error(command_generation, "틸팅 ESP32로 영점 복귀 명령을 전송하지 못했습니다.")
            return
        if not await self._is_current_motion(command_generation):
            return
        await self._replace_snapshot_if_current(
            command_generation,
            detail="0단계 영점을 맞추려고 끝까지 내리는 중입니다.",
            last_error=None,
        )
        await self._publish_status()

    async def _send_move(self, level: int, command_generation: int) -> None:
        self._homing_generation = None
        if level == self._settings.min_level:
            await self._send_home(command_generation)
            return
        target_mm = self._levels.target_mm_for_level(level)
        if target_mm is None:
            await self._mark_motion_error(command_generation, "목표 위치가 설정되지 않았습니다.")
            return
        if not await self._is_current_motion(command_generation):
            return
        if self._link.connection_generation != self._synced_generation:
            await self._mark_motion_error(command_generation, "틸팅 ESP32 보정이 유실됐습니다.")
            return

        sent = await self._link.write_line(
            f"MOVE_TO {target_mm:.2f} {self._settings.move_duty_percent}"
        )
        if not sent:
            await self._mark_motion_error(command_generation, "틸팅 ESP32로 이동 명령을 전송하지 못했습니다.")
            return
        if not await self._is_current_motion(command_generation):
            # STOP이 write와 경합하면 후속 이동을 상태로 확정하지 않는다.
            return
        await self._replace_snapshot_if_current(
            command_generation,
            detail=f"{level}단계로 이동 중입니다.",
            last_error=None,
        )
        await self._publish_status()

    async def _stop_motion(self, detail: str) -> None:
        async with self._state_lock:
            self._command_generation += 1
            motion_task = self._motion_task
            self._motion_task = None
            was_moving = self._snapshot.state is TiltState.MOVING
            pending = self._pending_calibration
            self._pending_calibration = None
            if pending is not None and not pending[3].done():
                pending[3].cancel()

        if motion_task is not None and not motion_task.done():
            motion_task.cancel()
        # motion task의 thread I/O 완료를 기다리기 전에 STOP을 보낸다.
        sent = await self._link.write_line("STOP") if self._running else False
        if motion_task is not None:
            await asyncio.gather(motion_task, return_exceptions=True)

        await self._replace_snapshot(
            state=TiltState.STOPPED if sent else TiltState.ERROR,
            target_level=None,
            position_valid=False if was_moving else self._snapshot.position_valid,
            position_mm=None if was_moving else self._snapshot.position_mm,
            detail=detail if sent else "STOP 명령 전송에 실패했습니다.",
            last_error=None if sent else "틸팅 ESP32로 STOP을 전송하지 못했습니다.",
        )
        await self._publish_status()

    async def _run_reader(self) -> None:
        while self._running:
            try:
                line = await self._link.read_line()
            except RuntimeError:
                return
            if not line:
                await asyncio.sleep(0)
                continue
            await self._handle_device_line(line)

    async def _handle_device_line(self, raw: bytes) -> None:
        try:
            payload = json.loads(raw.decode("utf-8", errors="replace").strip())
        except json.JSONDecodeError:
            return
        if not isinstance(payload, dict):
            return

        event = payload.get("event")
        if not isinstance(event, str):
            return
        firmware = payload.get("firmware")
        firmware_value = firmware if isinstance(firmware, str) else self._snapshot.firmware
        position_valid = payload.get("position_valid")
        valid_position = position_valid if isinstance(position_valid, bool) else False
        raw_position = payload.get("position_mm")
        position_mm = self._parse_position(raw_position) if valid_position else None
        if valid_position and position_mm is None:
            valid_position = False

        if event == "calibrated":
            await self._resolve_calibration_ack(payload)
            return
        if event == "ready":
            await self._handle_ready_or_status(firmware_value, valid_position, position_mm)
            return
        if event == "status":
            # 서버 재시작이 ESP32 부팅보다 늦으면 ready 이벤트를 놓친다. heartbeat도
            # 같은 동기화 입력으로 처리해 현재 위치가 유효할 때 보정을 재주입한다.
            await self._handle_ready_or_status(
                firmware_value, valid_position, position_mm, heartbeat=True
            )
            return
        if event == "moving":
            await self._replace_snapshot(
                firmware=firmware_value,
                position_valid=valid_position,
                position_mm=position_mm,
            )
            await self._publish_status()
            return
        if event == "at_target":
            await self._handle_at_target(firmware_value, valid_position, position_mm)
            return
        if event == "stopped":
            reason = payload.get("reason")
            await self._handle_stopped(
                firmware_value,
                valid_position,
                position_mm,
                str(reason) if reason is not None else "unknown",
            )
            return
        if event in {"fault", "rejected"}:
            reason = str(payload.get("reason", "알 수 없는 오류"))
            await self._replace_snapshot(
                state=TiltState.ERROR,
                target_level=None,
                firmware=firmware_value,
                position_valid=valid_position,
                position_mm=position_mm,
                detail="틸팅 ESP32가 명령을 거부하거나 오류를 보고했습니다.",
                last_error=reason,
            )
            await self._publish_status()

    async def _handle_ready_or_status(
        self,
        firmware: str | None,
        position_valid: bool,
        position_mm: float | None,
        *,
        heartbeat: bool = False,
    ) -> None:
        # 이동 중에도 heartbeat는 계속 온다. 그때의 position_valid=false는 아직
        # 목적지에 닿지 않았다는 뜻이지 장치가 죽었다는 뜻이 아니므로, 진행 중인
        # 이동을 ERROR로 갈아엎지 않는다. 이동의 끝은 at_target/stopped가 알린다.
        if heartbeat and self._snapshot.state is TiltState.MOVING:
            await self._replace_snapshot(firmware=firmware, position_mm=position_mm)
            return
        await self._replace_snapshot(
            state=TiltState.ERROR,
            target_level=None,
            firmware=firmware,
            position_valid=position_valid,
            position_mm=position_mm,
            detail=("틸팅 ESP32 보정을 준비합니다." if position_valid else "틸팅 ESP32 위치가 확인되지 않았습니다."),
            last_error=None if position_valid else "position_valid=false",
        )
        if position_valid:
            await self._start_prepare_task()
        else:
            await self._start_auto_home()
        await self._publish_status()

    async def _start_auto_home(self) -> None:
        """위치를 모르는 채 올라온 장치를 스스로 0단계로 내려 되살린다.

        장치는 연결될 때마다 위치를 모르는 상태로 부팅한다. 사람이 직접
        0단계를 누르기 전까지는 어떤 이동도 못 하므로, 연결 세대마다 한 번만
        영점 복귀를 걸어 준다. 실패해도 다시 시도하지 않아 계속 움직이지 않는다.
        """

        generation = self._link.connection_generation
        if not self._running or self._auto_home_generation == generation:
            return
        self._auto_home_generation = generation
        self._task_manager.create(
            TILT_AUTO_HOME_TASK_NAME,
            self._run_auto_home(),
            critical=False,
        )

    async def _run_auto_home(self) -> None:
        try:
            await self.set_target(self._settings.min_level)
        except TiltCommandRejectedError as error:
            LOGGER.warning(
                "틸팅 자동 영점 복귀를 시작하지 못했습니다.",
                extra={"component": "tilt", "event": "auto_home_rejected", "detail": str(error)},
            )

    async def _start_prepare_task(self) -> None:
        async with self._state_lock:
            if not self._running:
                return
            if self._prepare_task is not None and not self._prepare_task.done():
                return
            self._prepare_task = self._task_manager.create(
                TILT_PREPARE_TASK_NAME,
                self._prepare_device(),
                critical=False,
            )

    async def _prepare_device(self) -> None:
        generation = self._link.connection_generation
        if generation <= 0:
            await self._mark_prepare_error("틸팅 ESP32 연결이 확인되지 않았습니다.")
            return
        synced = await self._ensure_calibration_synced(generation)
        if not synced:
            await self._mark_prepare_error("틸팅 ESP32 보정을 완료하지 못했습니다.")
            return
        async with self._state_lock:
            if not self._running or generation != self._link.connection_generation:
                return
            snapshot = self._snapshot
            if not snapshot.position_valid:
                return
            self._set_snapshot_locked(
                replace(
                    snapshot,
                    state=TiltState.IDLE,
                    target_level=None,
                    detail="틸팅 ESP32가 이동 준비를 마쳤습니다.",
                    last_error=None,
                )
            )
        await self._publish_status()

    async def _ensure_calibration_synced(self, generation: int) -> bool:
        if generation == self._synced_generation:
            return True
        for duty, direction, speed in self._levels.calibration_snapshot():
            if not self._running or generation != self._link.connection_generation:
                return False
            loop = asyncio.get_running_loop()
            future: asyncio.Future[None] = loop.create_future()
            async with self._state_lock:
                self._pending_calibration = (duty, direction, generation, future)
            sent = await self._link.write_line(f"CALIBRATE {duty} {speed:.4f} {direction}")
            if not sent or generation != self._link.connection_generation:
                await self._clear_pending_calibration(future)
                return False
            try:
                async with asyncio.timeout(self._settings.event_timeout_seconds):
                    await future
            except asyncio.CancelledError:
                await self._clear_pending_calibration(future)
                raise
            except TimeoutError:
                await self._clear_pending_calibration(future)
                return False
            await self._clear_pending_calibration(future)
        self._synced_generation = generation
        return True

    async def _resolve_calibration_ack(self, payload: dict[str, object]) -> None:
        duty = payload.get("duty")
        direction = payload.get("direction")
        if isinstance(duty, bool) or not isinstance(duty, int) or not isinstance(direction, str):
            return
        async with self._state_lock:
            pending = self._pending_calibration
            if pending is None:
                return
            expected_duty, expected_direction, generation, future = pending
            if (
                duty == expected_duty
                and direction == expected_direction
                and generation == self._link.connection_generation
                and not future.done()
            ):
                future.set_result(None)

    async def _clear_pending_calibration(self, future: asyncio.Future[None]) -> None:
        async with self._state_lock:
            if self._pending_calibration is not None and self._pending_calibration[3] is future:
                self._pending_calibration = None

    async def _handle_at_target(
        self,
        firmware: str | None,
        position_valid: bool,
        position_mm: float | None,
    ) -> None:
        async with self._state_lock:
            snapshot = self._snapshot
            if snapshot.target_level is None or not position_valid:
                self._set_snapshot_locked(
                    replace(
                        snapshot,
                        state=TiltState.ERROR,
                        target_level=None,
                        firmware=firmware,
                        position_valid=position_valid,
                        position_mm=position_mm,
                        detail="목표 도달 위치를 확인하지 못했습니다.",
                        last_error="at_target 위치가 유효하지 않습니다.",
                    )
                )
            else:
                self._set_snapshot_locked(
                    replace(
                        snapshot,
                        state=TiltState.AT_TARGET,
                        level=snapshot.target_level,
                        target_level=None,
                        firmware=firmware,
                        position_valid=True,
                        position_mm=position_mm,
                        detail=f"{snapshot.target_level}단계에 도달했습니다.",
                        last_error=None,
                    )
                )
        await self._publish_status()

    async def _handle_stopped(
        self,
        firmware: str | None,
        position_valid: bool,
        position_mm: float | None,
        reason: str,
    ) -> None:
        # 0단계 영점 복귀는 RUN으로 내려가므로 firmware가 위치를 버린 채 끝난다.
        # 이때는 오류가 아니라, 지금 닿은 바닥을 새 영점으로 선언할 차례다.
        if reason == "manual_complete" and self._homing_generation is not None:
            await self._finish_homing(firmware)
            return

        timeout = reason in {"timeout", "motion_timeout", "fault"}
        async with self._state_lock:
            previous = self._snapshot
            disconnected_while_moving = previous.state is TiltState.MOVING and not position_valid
            state = TiltState.ERROR if timeout or disconnected_while_moving else TiltState.STOPPED
            self._set_snapshot_locked(
                replace(
                    previous,
                    state=state,
                    level=None if timeout or disconnected_while_moving else previous.level,
                    target_level=None,
                    firmware=firmware,
                    position_valid=False if timeout or disconnected_while_moving else position_valid,
                    position_mm=position_mm,
                    detail=(
                        "틸팅 이동이 시간 초과로 정지했습니다."
                        if timeout
                        else "연결 단절 중 틸팅 위치를 잃었습니다."
                        if disconnected_while_moving
                        else "틸팅 이동이 정지했습니다."
                    ),
                    last_error=(
                        reason
                        if timeout
                        else "serial_disconnect"
                        if disconnected_while_moving
                        else None
                    ),
                )
            )
        if position_valid and state is TiltState.STOPPED:
            await self._start_prepare_task()
        await self._publish_status()

    async def _finish_homing(self, firmware: str | None) -> None:
        """바닥에 닿은 지점을 0으로 선언해 누적 오차를 없앤다."""

        self._homing_generation = None
        target_mm = self._levels.target_mm_for_level(self._settings.min_level) or 0.0
        if not await self._link.write_line(f"SET_POSITION {target_mm:.2f}"):
            await self._replace_snapshot(
                state=TiltState.ERROR,
                target_level=None,
                position_valid=False,
                firmware=firmware,
                detail="영점을 다시 잡지 못했습니다.",
                last_error="틸팅 ESP32로 SET_POSITION을 전송하지 못했습니다.",
            )
            await self._publish_status()
            return
        # firmware가 곧 ready 상태를 보내지만, 화면이 먼저 결과를 알 수 있게 한다.
        await self._replace_snapshot(
            state=TiltState.AT_TARGET,
            level=self._settings.min_level,
            target_level=None,
            position_mm=target_mm,
            position_valid=True,
            firmware=firmware,
            detail=f"{self._settings.min_level}단계에서 영점을 다시 맞췄습니다.",
            last_error=None,
        )
        await self._publish_status()

    async def _mark_prepare_error(self, detail: str) -> None:
        await self._replace_snapshot(
            state=TiltState.ERROR,
            target_level=None,
            detail=detail,
            last_error=detail,
        )
        await self._publish_status()

    async def _mark_motion_error(self, command_generation: int, detail: str) -> None:
        async with self._state_lock:
            if command_generation != self._command_generation:
                return
            self._set_snapshot_locked(
                replace(
                    self._snapshot,
                    state=TiltState.ERROR,
                    target_level=None,
                    detail=detail,
                    last_error=detail,
                )
            )
        await self._publish_status()

    async def _replace_snapshot_if_current(
        self,
        command_generation: int,
        **changes: object,
    ) -> None:
        async with self._state_lock:
            if command_generation != self._command_generation:
                return
            self._set_snapshot_locked(replace(self._snapshot, **changes))

    async def _is_current_motion(self, command_generation: int) -> bool:
        async with self._state_lock:
            return self._running and command_generation == self._command_generation

    async def _replace_snapshot(self, **changes: object) -> None:
        async with self._state_lock:
            self._set_snapshot_locked(replace(self._snapshot, **changes))

    def _set_snapshot_locked(self, snapshot: TiltSnapshot) -> None:
        self._snapshot = replace(snapshot, updated_at=utc_now())

    async def _publish_notice(self, detail: str) -> None:
        LOGGER.warning(detail, extra={"component": "tilt", "event": "tilt_command_rejected"})
        await self._publish_status()

    async def _publish_status(self) -> None:
        snapshot = self._snapshot
        message = TiltStatusMessage(
            state=snapshot.state,
            level=snapshot.level,
            target_level=snapshot.target_level,
            position_mm=snapshot.position_mm,
            position_valid=snapshot.position_valid,
            firmware=snapshot.firmware,
            detail=snapshot.detail,
            last_error=snapshot.last_error,
            updated_at=snapshot.updated_at,
        )
        try:
            await self._mqtt.publish(
                TILT_STATUS_TOPIC,
                message.model_dump_json(),
                qos=1,
                retain=True,
            )
        except MqttUnavailableError:
            LOGGER.warning(
                "틸팅 상태를 MQTT로 발행하지 못했습니다.",
                extra={"component": "tilt", "event": "tilt_status_publish_failed"},
                exc_info=True,
            )

    @staticmethod
    async def _cancel_tasks(*tasks: asyncio.Task[None] | None) -> None:
        pending = [task for task in tasks if task is not None and not task.done()]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    @staticmethod
    def _parse_position(value: object) -> float | None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        position = float(value)
        return position if math.isfinite(position) else None

    @staticmethod
    def _summarize_validation_error(error: ValidationError) -> str:
        first = error.errors(include_url=False)[0]
        location = ".".join(str(item) for item in first["loc"])
        detail = str(first["msg"])
        return f"{location}: {detail}" if location else detail
