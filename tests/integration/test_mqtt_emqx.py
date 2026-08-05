"""로컬 EMQX와 실제 MQTT 발행·구독 왕복을 검증한다."""

from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import pytest

from smart_desk.config.settings import MqttSettings
from smart_desk.core.task_manager import TaskManager
from smart_desk.modules.mqtt.client import MqttClient
from smart_desk.modules.mqtt.models import MqttMessage


pytestmark = [
    pytest.mark.mqtt_integration,
    pytest.mark.skipif(
        os.getenv("SMART_DESK_RUN_MQTT_INTEGRATION") != "1",
        reason="로컬 MQTT 통합 테스트를 명시적으로 요청하지 않았습니다.",
    ),
]


async def test_real_emqx_publish_subscribe_round_trip() -> None:
    task_manager = TaskManager()
    client = MqttClient(
        MqttSettings(
            host="127.0.0.1",
            port=1883,
            client_id=f"smart-desk-test-{uuid4().hex}",
            operation_timeout_seconds=3,
            reconnect_interval_seconds=0.1,
        ),
        task_manager,
    )
    topic = f"/smartdesk/test/mqtt/{uuid4().hex}"
    payload = uuid4().hex.encode()
    received: asyncio.Future[MqttMessage] = asyncio.Future()

    async def handler(message: MqttMessage) -> None:
        if not received.done():
            received.set_result(message)

    client.register_handler(topic, handler, qos=1)
    try:
        await client.start()
        await client.publish(topic, payload, qos=1, retain=False)
        async with asyncio.timeout(3):
            message = await received

        assert message.topic == topic
        assert message.payload == payload
        assert message.qos == 1
        assert message.retained is False
    finally:
        await client.stop()
        await task_manager.shutdown()


async def test_real_emqx_reconnects_and_resubscribes_after_client_disconnect() -> None:
    task_manager = TaskManager()
    client = MqttClient(
        MqttSettings(
            host="127.0.0.1",
            port=1883,
            client_id=f"smart-desk-reconnect-test-{uuid4().hex}",
            operation_timeout_seconds=3,
            reconnect_interval_seconds=0.05,
        ),
        task_manager,
    )
    topic = f"/smartdesk/test/mqtt/reconnect/{uuid4().hex}"
    payload = uuid4().hex.encode()
    received: asyncio.Future[MqttMessage] = asyncio.Future()

    async def handler(message: MqttMessage) -> None:
        if not received.done():
            received.set_result(message)

    client.register_handler(topic, handler, qos=1)
    try:
        await client.start()
        first_connection = client._client
        assert first_connection is not None

        first_connection._client.disconnect()
        async with asyncio.timeout(3):
            while client._client is first_connection or not client.is_connected():
                await asyncio.sleep(0.01)

        await client.publish(topic, payload, qos=1, retain=False)
        async with asyncio.timeout(3):
            message = await received

        assert message.payload == payload
    finally:
        await client.stop()
        await task_manager.shutdown()
