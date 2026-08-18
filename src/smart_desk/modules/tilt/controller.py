"""틸팅 MQTT 명령을 받아 ESP32 시리얼 프로토콜로 변환하고 상태를 발행한다."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
import json
import logging

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
# MOVE_TO 완료를 알리는 종결 이벤트. 그 외(moving/move_to/extended)는 아직
# 모터가 도는 중이라는 뜻이라 controller 공개 상태를 바꾸지 않는다.
_TERMINAL_EVENTS = {"stopped", "at_target", "rejected"}
_NON_TERMINAL_MOTION_EVENTS = {"moving", "move_to", "extended"}


def utc_now() -> datetime:
    """현재 timezone-aware UTC 시각을 반환한다."""

    return datetime.now(UTC)


class TiltController:
    """틸팅 ESP32(모터드라이버)의 단일 소유자로 MQTT 명령을 시리얼로 중계한다.

    `DeskController`와 달리 폐루프 높이 센서가 없다 — 펌웨어 자신이
    open-loop 위치 추정과 MOVE_TO 시간 계산을 소유하므로, 이 controller는
    명령 중계·중복 실행 방지·상태 발행만 책임진다.
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
            state=TiltState.IDLE,
            level=None,
            position_mm=None,
            firmware=None,
            detail="틸팅 제어기가 시작되지 않았습니다.",
            last_error=None,
            updated_at=utc_now(),
        )
        self._running = False
        self._command_lock = asyncio.Lock()
        self._reader_task: asyncio.Task[None] | None = None
        self._synced_generation = -1

    async def start(self) -> None:
        """시리얼 link를 시작하고 백그라운드 리더 task를 생성한다."""

        if self._running:
            raise RuntimeError("틸팅 제어기가 이미 실행 중입니다.")
        await self._link.start()
        self._running = True
        self._synced_generation = -1
        self._set_snapshot(
            replace(
                self._snapshot,
                state=TiltState.IDLE,
                detail="틸팅 제어기를 시작했습니다.",
                last_error=None,
            )
        )
        try:
            self._reader_task = self._task_manager.create(
                TILT_READER_TASK_NAME,
                self._run(),
                critical=False,
            )
        except Exception:
            self._running = False
            await self._link.stop()
            raise

    async def stop(self) -> None:
        """리더 task를 취소한 뒤 시리얼 link를 종료한다."""

        self._running = False
        task = self._reader_task
        self._reader_task = None
        if task is not None:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await self._link.stop()

    async def handle_command(self, message: MqttMessage) -> None:
        """TILT_COMMAND_TOPIC에서 받은 GOTO/STOP 명령을 실행한다."""

        try:
            command = TiltCommandAdapter.validate_json(message.payload)
        except ValidationError as error:
            LOGGER.warning(
                "유효하지 않은 틸팅 명령을 무시했습니다.",
                extra={"component": "tilt", "event": "tilt_command_invalid"},
            )
            await self._publish_rejected(self._summarize_validation_error(error))
            return

        if isinstance(command, TiltStopCommand):
            await self._stop_motion("MQTT STOP 명령을 받았습니다.")
            return

        await self._goto_level(command)

    def get_snapshot(self) -> TiltSnapshot:
        """I/O 없이 현재 틸팅 상태를 반환한다."""

        return self._snapshot

    async def _goto_level(self, command: TiltGotoCommand) -> None:
        async with self._command_lock:
            if not self._running:
                await self._publish_rejected_locked("틸팅 제어기가 실행 중이 아닙니다.")
                return
            if self._snapshot.state is TiltState.MOVING:
                await self._publish_rejected_locked("다른 이동이 진행 중입니다.")
                return
            if not self._settings.min_level <= command.level <= self._settings.max_level:
                await self._publish_rejected_locked(
                    f"단계는 {self._settings.min_level}~{self._settings.max_level} "
                    "사이여야 합니다."
                )
                return
            target_mm = self._levels.target_mm_for_level(command.level)
            if target_mm is None:
                await self._publish_rejected_locked(
                    f"{command.level}단계의 목표 위치가 설정되지 않았습니다."
                )
                return

            await self._ensure_calibration_synced()

            sent = await self._link.write_line(
                f"MOVE_TO {target_mm:.2f} {self._settings.move_duty_percent}"
            )
            if not sent:
                await self._publish_rejected_locked(
                    "틸팅 ESP32로 이동 명령을 전송하지 못했습니다."
                )
                return

            self._set_snapshot(
                replace(
                    self._snapshot,
                    state=TiltState.MOVING,
                    level=command.level,
                    detail=f"{command.level}단계로 이동합니다.",
                    last_error=None,
                )
            )
        await self._publish_status()

    async def _stop_motion(self, detail: str) -> None:
        async with self._command_lock:
            if not self._running:
                return
            sent = await self._link.write_line("STOP")
            self._set_snapshot(
                replace(
                    self._snapshot,
                    state=TiltState.IDLE if sent else TiltState.ERROR,
                    detail=detail,
                    last_error=None if sent else "STOP 명령 전송에 실패했습니다.",
                )
            )
        await self._publish_status()

    async def _ensure_calibration_synced(self) -> None:
        """연결이 새로 열렸을 때만 보정 테이블을 ESP32에 재전송한다."""

        if self._link.connection_generation == self._synced_generation:
            return
        for duty, direction, speed in self._levels.calibration_snapshot():
            await self._link.write_line(f"CALIBRATE {duty} {speed:.4f} {direction}")
        self._synced_generation = self._link.connection_generation

    async def _run(self) -> None:
        while self._running:
            line = await self._link.read_line()
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
        is_known_event = event in _TERMINAL_EVENTS or event in _NON_TERMINAL_MOTION_EVENTS
        if not is_known_event and "firmware" not in payload:
            # 인식하지 못하는 라인(파싱 잡음 등)은 조용히 무시한다.
            return

        async with self._command_lock:
            firmware = payload.get("firmware")
            firmware = firmware if isinstance(firmware, str) else self._snapshot.firmware
            position_mm = payload.get("position_mm")
            position_mm = (
                float(position_mm)
                if isinstance(position_mm, (int, float))
                else self._snapshot.position_mm
            )

            if event == "stopped":
                self._set_snapshot(
                    replace(
                        self._snapshot,
                        state=TiltState.IDLE,
                        firmware=firmware,
                        position_mm=position_mm,
                        detail=f"{self._snapshot.level}단계 이동이 끝났습니다.",
                        last_error=None,
                    )
                )
            elif event == "at_target":
                self._set_snapshot(
                    replace(
                        self._snapshot,
                        state=TiltState.IDLE,
                        firmware=firmware,
                        position_mm=position_mm,
                        detail="이미 목표 위치입니다.",
                        last_error=None,
                    )
                )
            elif event == "rejected":
                reason = payload.get("reason", "알 수 없는 오류")
                self._set_snapshot(
                    replace(
                        self._snapshot,
                        state=TiltState.ERROR,
                        firmware=firmware,
                        detail="틸팅 ESP32가 명령을 거부했습니다.",
                        last_error=str(reason),
                    )
                )
            elif event in _NON_TERMINAL_MOTION_EVENTS:
                self._set_snapshot(replace(self._snapshot, firmware=firmware))
                return
            else:
                # STATUS 스냅샷: 제어 상태는 바꾸지 않고 진단 정보만 갱신한다.
                self._set_snapshot(
                    replace(self._snapshot, firmware=firmware, position_mm=position_mm)
                )
                return
        await self._publish_status()

    def _set_snapshot(self, snapshot: TiltSnapshot) -> None:
        self._snapshot = replace(snapshot, updated_at=utc_now())

    async def _publish_rejected_locked(self, detail: str) -> None:
        """`_command_lock`을 이미 쥔 호출자 안에서 상태를 거부로 바꾸고 발행한다."""

        self._set_snapshot(
            replace(
                self._snapshot,
                state=TiltState.ERROR,
                detail=detail,
                last_error=detail,
            )
        )
        await self._publish_status()

    async def _publish_rejected(self, detail: str) -> None:
        async with self._command_lock:
            await self._publish_rejected_locked(detail)

    async def _publish_status(self) -> None:
        snapshot = self._snapshot
        message = TiltStatusMessage(
            state=snapshot.state,
            level=snapshot.level,
            position_mm=snapshot.position_mm,
            firmware=snapshot.firmware,
            detail=snapshot.detail,
            last_error=snapshot.last_error,
            updated_at=snapshot.updated_at,
        )
        try:
            await self._mqtt.publish(
                TILT_STATUS_TOPIC,
                message.model_dump_json(),
                qos=0,
                retain=True,
            )
        except MqttUnavailableError:
            LOGGER.warning(
                "틸팅 상태를 MQTT로 발행하지 못했습니다.",
                extra={"component": "tilt", "event": "tilt_status_publish_failed"},
                exc_info=True,
            )

    @staticmethod
    def _summarize_validation_error(error: ValidationError) -> str:
        first = error.errors(include_url=False)[0]
        location = ".".join(str(item) for item in first["loc"])
        detail = str(first["msg"])
        return f"{location}: {detail}" if location else detail
