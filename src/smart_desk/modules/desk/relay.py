"""ESP32 릴레이 MQTT 명령과 상태 계약을 캡슐화한다."""

from __future__ import annotations

import logging

from pydantic import ValidationError

from smart_desk.modules.desk.messages import (
    RelayPulseMessage,
    RelayStatusMessage,
    RelayStopMessage,
    RelayWakeMessage,
)
from smart_desk.modules.desk.models import Direction, RelaySnapshot
from smart_desk.modules.mqtt.client import MqttClient
from smart_desk.modules.mqtt.models import MqttMessage
from smart_desk.modules.mqtt.topics import ESP32_COMMAND_TOPIC


LOGGER = logging.getLogger(__name__)


class RelayClient:
    """ESP32 wire JSON을 발행하고 마지막 live relay 상태를 보관한다."""

    def __init__(self, mqtt: MqttClient) -> None:
        self._mqtt = mqtt
        self._snapshot = RelaySnapshot(
            event=None,
            state=None,
            firmware=None,
            code=None,
            detail=None,
            received_at=None,
            last_error=None,
        )

    async def handle_status(self, message: MqttMessage) -> None:
        """retained를 무시하고 유효한 ESP32 live 상태만 snapshot에 반영한다."""

        if message.retained:
            LOGGER.debug(
                "retained ESP32 상태를 무시했습니다.",
                extra={"component": "relay", "event": "relay_status_ignored"},
            )
            return

        try:
            status = RelayStatusMessage.model_validate_json(message.payload)
        except ValidationError as error:
            self._snapshot = RelaySnapshot(
                event=self._snapshot.event,
                state=self._snapshot.state,
                firmware=self._snapshot.firmware,
                code=self._snapshot.code,
                detail=self._snapshot.detail,
                received_at=self._snapshot.received_at,
                last_error=self._summarize_validation_error(error),
            )
            LOGGER.warning(
                "유효하지 않은 ESP32 상태를 무시했습니다.",
                extra={"component": "relay", "event": "relay_status_invalid"},
            )
            return

        self._snapshot = RelaySnapshot(
            event=status.event,
            state=status.state,
            firmware=status.firmware,
            code=status.code,
            detail=status.detail,
            received_at=message.received_at,
            last_error=None,
        )

    async def pulse(self, direction: Direction, hold_ms: int) -> None:
        """검증된 UP/DOWN deadline 부여·연장 명령 하나를 발행한다."""

        if not isinstance(direction, Direction):
            raise TypeError(
                "릴레이 pulse 방향은 Direction.UP 또는 Direction.DOWN이어야 합니다."
            )
        if isinstance(hold_ms, bool) or not isinstance(hold_ms, int):
            raise TypeError("릴레이 hold_ms는 bool이 아닌 정수여야 합니다.")
        if not 50 <= hold_ms <= 500:
            raise ValueError("릴레이 hold_ms는 50~500ms여야 합니다.")

        message = RelayPulseMessage(command=direction, hold_ms=hold_ms)
        await self._mqtt.publish(
            ESP32_COMMAND_TOPIC,
            message.model_dump_json(),
            qos=0,
            retain=False,
        )

    async def send_stop(self) -> None:
        """추가 필드 없는 정확한 ESP32 STOP 명령을 발행한다."""

        await self._mqtt.publish(
            ESP32_COMMAND_TOPIC,
            RelayStopMessage().model_dump_json(),
            qos=0,
            retain=False,
        )

    async def wake(self, direction: Direction, basis_height_cm: float) -> None:
        """cache 높이를 근거로 센서를 깨우는 정확히 한 번의 400ms pulse를 발행한다."""

        if not isinstance(direction, Direction):
            raise TypeError("WAKE 방향은 Direction.UP 또는 Direction.DOWN이어야 합니다.")
        message = RelayWakeMessage(
            direction=direction,
            basis_height_cm=basis_height_cm,
        )
        await self._mqtt.publish(
            ESP32_COMMAND_TOPIC,
            message.model_dump_json(),
            qos=0,
            retain=False,
        )

    def get_snapshot(self) -> RelaySnapshot:
        """네트워크 I/O 없이 마지막 ESP32 상태를 반환한다."""

        return self._snapshot

    @staticmethod
    def _summarize_validation_error(error: ValidationError) -> str:
        first = error.errors(include_url=False)[0]
        location = ".".join(str(item) for item in first["loc"])
        detail = str(first["msg"])
        return f"{location}: {detail}" if location else detail
