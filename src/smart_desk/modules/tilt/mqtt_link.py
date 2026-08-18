"""틸팅 ESP32와 MQTT로 주고받는 장치 링크다.

`TiltSerialLink`와 같은 계약(start/stop/write_line/read_line/
connection_generation)을 제공해 `TiltController`를 그대로 쓴다. 다른 점은
선이 아니라 broker를 지난다는 것뿐이고, 오가는 내용은 시리얼 때와 똑같은
텍스트 명령과 JSON 이벤트 줄이다.

연결 여부는 장치가 보내는 이벤트로 판단한다. 한동안 아무 말이 없으면 끊긴
것으로 보고, 다시 말을 걸어오면 새 연결 세대로 올려 상위에서 보정을 다시
주입하게 한다.
"""

from __future__ import annotations

import asyncio
import logging
import time

from smart_desk.config.settings import TiltSettings
from smart_desk.modules.mqtt.client import MqttClient, MqttUnavailableError
from smart_desk.modules.mqtt.models import MqttMessage
from smart_desk.modules.mqtt.topics import (
    TILT_DEVICE_COMMAND_TOPIC,
    TILT_DEVICE_STATUS_TOPIC,
)
from smart_desk.modules.tilt.serial_link import TiltLinkSnapshot, TiltLinkStatus


LOGGER = logging.getLogger(__name__)
# 이 시간 동안 장치가 아무 말도 없으면 끊긴 것으로 본다. firmware는 상태를
# 주기적으로 보내므로, 그보다 넉넉히 잡는다.
DEVICE_SILENCE_TIMEOUT_SECONDS = 15.0
# 밀린 이벤트가 무한정 쌓이지 않게 한다. 넘치면 가장 오래된 것부터 버린다.
INBOX_MAX_LINES = 256


class TiltMqttLink:
    """틸팅 ESP32의 MQTT 장치 링크. 시리얼 링크와 같은 자리에 끼운다."""

    def __init__(
        self,
        mqtt: MqttClient,
        settings: TiltSettings,
        *,
        monotonic=time.monotonic,
        silence_timeout_seconds: float = DEVICE_SILENCE_TIMEOUT_SECONDS,
    ) -> None:
        self._mqtt = mqtt
        self._settings = settings
        self._monotonic = monotonic
        self._silence_timeout = silence_timeout_seconds
        self._inbox: asyncio.Queue[bytes] = asyncio.Queue(maxsize=INBOX_MAX_LINES)
        self._started = False
        self._connection_generation = 0
        self._last_seen: float | None = None
        self._last_error: str | None = None
        # MqttClient는 시작 전에만 handler를 받는다. 구독은 여기서 걸어 두고
        # start()는 링크를 여는 일만 한다.
        mqtt.register_handler(TILT_DEVICE_STATUS_TOPIC, self._handle_device_message, qos=1)

    @property
    def connection_generation(self) -> int:
        """장치가 새로 인사할 때마다 올라간다. 보정 재주입 시점을 알린다."""

        return self._connection_generation

    async def start(self) -> None:
        if self._started:
            raise RuntimeError("틸팅 MQTT link가 이미 실행 중입니다.")
        self._started = True
        self._last_seen = None
        self._last_error = None
        # 연결이 살아 있다면 안전을 위해 먼저 멈춘다. 실패해도 시작은 막지 않는다.
        await self.write_line_if_connected("STOP")

    async def stop(self) -> None:
        self._started = False
        self._last_seen = None
        while not self._inbox.empty():
            self._inbox.get_nowait()

    async def _handle_device_message(self, message: MqttMessage) -> None:
        """장치가 보낸 줄을 그대로 상위로 넘긴다."""

        if not self._started:
            return
        was_silent = not self._is_live()
        self._last_seen = self._monotonic()
        if was_silent:
            # 처음 인사했거나, 조용하다 돌아왔다. 상위가 보정을 다시 넣게 한다.
            self._connection_generation += 1
        payload = message.payload if isinstance(message.payload, bytes) else str(message.payload).encode()
        for raw in payload.splitlines():
            if not raw.strip():
                continue
            if self._inbox.full():
                self._inbox.get_nowait()
            self._inbox.put_nowait(raw)

    def _is_live(self) -> bool:
        if self._last_seen is None:
            return False
        return (self._monotonic() - self._last_seen) <= self._silence_timeout

    async def write_line(self, command: str) -> bool:
        """장치 명령 토픽으로 한 줄을 보낸다."""

        if not self._started:
            raise RuntimeError("MQTT link를 시작한 뒤 써야 합니다.")
        try:
            await self._mqtt.publish(TILT_DEVICE_COMMAND_TOPIC, command, qos=1, retain=False)
        except (MqttUnavailableError, Exception) as error:  # noqa: B014 - broker 오류는 모두 링크 실패다
            self._last_error = str(error)
            LOGGER.warning(
                "틸팅 MQTT 명령을 보내지 못했습니다.",
                extra={"component": "tilt_mqtt", "event": "tilt_mqtt_publish_failed"},
            )
            return False
        self._last_error = None
        return True

    async def write_line_if_connected(self, command: str) -> bool:
        """연결이 이미 있을 때만 보낸다. 종료 경로가 새 연결을 만들지 않게 한다."""

        if not self._mqtt.is_connected():
            return False
        return await self.write_line(command)

    async def read_line(self, timeout_seconds: float | None = None) -> bytes:
        """장치가 보낸 다음 줄을 돌려주고, 조용하면 빈 bytes를 돌려준다."""

        if not self._started:
            raise RuntimeError("MQTT link를 시작한 뒤 읽어야 합니다.")
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
        status = TiltLinkStatus.CONNECTED if self._is_live() else TiltLinkStatus.DISCONNECTED
        return TiltLinkSnapshot(status, None)
