"""틸팅 MQTT 장치 링크가 시리얼 링크와 같은 계약을 지키는지 확인한다."""

from __future__ import annotations

import pytest

from smart_desk.config.settings import TiltSettings
from datetime import UTC, datetime

from smart_desk.modules.mqtt.models import MqttMessage
from smart_desk.modules.mqtt.topics import (
    TILT_DEVICE_COMMAND_TOPIC,
    TILT_DEVICE_STATUS_TOPIC,
)
from smart_desk.modules.tilt.mqtt_link import TiltMqttLink
from smart_desk.modules.tilt.serial_link import TiltLinkStatus


class Clock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class FakeMqtt:
    def __init__(self, *, connected: bool = True) -> None:
        self.published: list[tuple[str, str, int, bool]] = []
        self.handlers: dict[str, object] = {}
        self.connected = connected
        self.fail = False

    def register_handler(self, topic: str, handler, *, qos: int = 0) -> None:  # type: ignore[no-untyped-def]
        self.handlers[topic] = handler

    def is_connected(self) -> bool:
        return self.connected

    async def publish(self, topic: str, payload, *, qos: int, retain: bool) -> None:  # type: ignore[no-untyped-def]
        if self.fail:
            raise RuntimeError("broker down")
        self.published.append((topic, payload, qos, retain))


def device_line(payload: str) -> MqttMessage:
    return MqttMessage(
        topic=TILT_DEVICE_STATUS_TOPIC,
        payload=payload.encode(),
        qos=1,
        retained=False,
        received_at=datetime.now(UTC),
    )


@pytest.fixture
def parts():
    clock = Clock()
    mqtt = FakeMqtt()
    link = TiltMqttLink(mqtt, TiltSettings(), monotonic=clock, silence_timeout_seconds=15.0)  # type: ignore[arg-type]
    return link, mqtt, clock


async def test_start_subscribes_and_sends_a_safety_stop(parts) -> None:
    link, mqtt, _clock = parts

    await link.start()

    assert TILT_DEVICE_STATUS_TOPIC in mqtt.handlers
    assert mqtt.published[0][0] == TILT_DEVICE_COMMAND_TOPIC
    assert mqtt.published[0][1] == "STOP"


async def test_commands_go_out_on_the_device_topic(parts) -> None:
    link, mqtt, _clock = parts
    await link.start()
    mqtt.published.clear()

    assert await link.write_line("MOVE_TO 38.00 100") is True
    assert mqtt.published == [(TILT_DEVICE_COMMAND_TOPIC, "MOVE_TO 38.00 100", 1, False)]


async def test_publish_failure_is_reported_and_marks_the_link(parts) -> None:
    link, mqtt, _clock = parts
    await link.start()
    mqtt.fail = True

    assert await link.write_line("STOP") is False
    assert link.get_snapshot().status is TiltLinkStatus.ERROR


async def test_device_lines_are_readable_in_order(parts) -> None:
    link, mqtt, _clock = parts
    await link.start()
    handler = mqtt.handlers[TILT_DEVICE_STATUS_TOPIC]

    await handler(device_line('{"event":"ready"}\n{"event":"status"}'))

    assert await link.read_line(0.1) == b'{"event":"ready"}'
    assert await link.read_line(0.1) == b'{"event":"status"}'
    # 더 없으면 시리얼 링크와 같이 빈 bytes를 준다.
    assert await link.read_line(0.05) == b""


async def test_generation_rises_when_a_silent_device_speaks_again(parts) -> None:
    link, mqtt, clock = parts
    await link.start()
    handler = mqtt.handlers[TILT_DEVICE_STATUS_TOPIC]

    await handler(device_line('{"event":"ready"}'))
    first = link.connection_generation
    assert first == 1

    # 계속 말하는 동안에는 같은 세대다.
    clock.advance(1.0)
    await handler(device_line('{"event":"status"}'))
    assert link.connection_generation == first

    # 오래 조용하다 돌아오면 새 연결로 본다.
    clock.advance(30.0)
    await handler(device_line('{"event":"ready"}'))
    assert link.connection_generation == first + 1


async def test_snapshot_follows_the_device_silence(parts) -> None:
    link, mqtt, clock = parts
    assert link.get_snapshot().status is TiltLinkStatus.STOPPED

    await link.start()
    assert link.get_snapshot().status is TiltLinkStatus.DISCONNECTED

    await mqtt.handlers[TILT_DEVICE_STATUS_TOPIC](device_line('{"event":"ready"}'))
    assert link.get_snapshot().status is TiltLinkStatus.CONNECTED

    clock.advance(30.0)
    assert link.get_snapshot().status is TiltLinkStatus.DISCONNECTED


async def test_shutdown_path_does_not_publish_without_a_broker(parts) -> None:
    link, mqtt, _clock = parts
    await link.start()
    mqtt.published.clear()
    mqtt.connected = False

    assert await link.write_line_if_connected("STOP") is False
    assert mqtt.published == []
