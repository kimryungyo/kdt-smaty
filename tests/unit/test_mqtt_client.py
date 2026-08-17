"""MqttClient 연결, 발행, 수신과 재연결 단위 테스트."""

from __future__ import annotations

import asyncio
from collections import deque
from datetime import UTC
from types import SimpleNamespace
from typing import Any

import aiomqtt
import pytest

from smart_desk.config.settings import MqttSettings
from smart_desk.core.task_manager import TaskManager
from smart_desk.modules.mqtt.client import (
    MqttClient,
    MqttStartupError,
    MqttUnavailableError,
)
from smart_desk.modules.mqtt.models import MqttMessage


class FakeMessageStream:
    """테스트가 메시지나 연결 오류를 순서대로 넣는 async iterator."""

    def __init__(self) -> None:
        self._items: asyncio.Queue[object] = asyncio.Queue()

    def __aiter__(self) -> FakeMessageStream:
        return self

    async def __anext__(self) -> Any:
        item = await self._items.get()
        if isinstance(item, BaseException):
            raise item
        return item

    def put(self, item: object) -> None:
        self._items.put_nowait(item)


class FakeBrokerClient:
    """aiomqtt Client에서 사용하는 최소 async context와 I/O를 재현한다."""

    def __init__(
        self,
        *,
        enter_error: BaseException | None = None,
        enter_blocker: asyncio.Event | None = None,
        subscribe_error: BaseException | None = None,
        publish_error: BaseException | None = None,
    ) -> None:
        self.enter_error = enter_error
        self.enter_blocker = enter_blocker
        self.subscribe_error = subscribe_error
        self.publish_error = publish_error
        self.messages = FakeMessageStream()
        self.subscriptions: list[tuple[str, int, float | None]] = []
        self.publications: list[dict[str, object]] = []
        self.entered = False
        self.exited = False

    async def __aenter__(self) -> FakeBrokerClient:
        if self.enter_blocker is not None:
            await self.enter_blocker.wait()
        if self.enter_error is not None:
            raise self.enter_error
        self.entered = True
        return self

    async def __aexit__(self, *_args: object) -> None:
        self.exited = True

    async def subscribe(
        self,
        topic: str,
        *,
        qos: int,
        timeout: float | None,
    ) -> None:
        if self.subscribe_error is not None:
            raise self.subscribe_error
        self.subscriptions.append((topic, qos, timeout))

    async def publish(
        self,
        topic: str,
        payload: bytes | str,
        *,
        qos: int,
        retain: bool,
        timeout: float | None,
    ) -> None:
        if self.publish_error is not None:
            raise self.publish_error
        self.publications.append(
            {
                "topic": topic,
                "payload": payload,
                "qos": qos,
                "retain": retain,
                "timeout": timeout,
            }
        )


class FakeClientFactory:
    """연결 시도마다 준비된 가짜 client를 하나씩 반환한다."""

    def __init__(self, *clients: FakeBrokerClient) -> None:
        self._clients = deque(clients)
        self.calls: list[dict[str, object]] = []

    def __call__(self, **kwargs: object) -> FakeBrokerClient:
        self.calls.append(kwargs)
        if not self._clients:
            raise AssertionError("준비된 가짜 MQTT client가 없습니다.")
        return self._clients.popleft()


def mqtt_settings(**overrides: object) -> MqttSettings:
    values: dict[str, object] = {
        "operation_timeout_seconds": 0.2,
        "reconnect_interval_seconds": 0.001,
    }
    values.update(overrides)
    return MqttSettings(**values)


async def wait_until(predicate, *, timeout: float = 0.5) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0)


def mqtt_message(
    topic: str,
    payload: bytes,
    *,
    qos: int = 1,
    retain: bool = False,
) -> SimpleNamespace:
    return SimpleNamespace(
        topic=SimpleNamespace(value=topic),
        payload=payload,
        qos=qos,
        retain=retain,
    )


def test_handler_registration_validates_topic_qos_and_duplicates() -> None:
    client = MqttClient(mqtt_settings(), TaskManager())

    async def handler(_message: MqttMessage) -> None:
        return None

    client.register_handler("/smartdesk/test", handler, qos=0)

    with pytest.raises(RuntimeError, match="이미 등록"):
        client.register_handler("/smartdesk/test", handler)
    with pytest.raises(ValueError, match="비어"):
        client.register_handler(" ", handler)
    with pytest.raises(ValueError, match="wildcard"):
        client.register_handler("/smartdesk/+", handler)
    for invalid_qos in (True, 1.0, 3):
        with pytest.raises(ValueError, match="QoS"):
            client.register_handler(  # type: ignore[arg-type]
                f"/smartdesk/invalid-qos/{invalid_qos}",
                handler,
                qos=invalid_qos,
            )


async def test_start_connects_with_v311_and_subscribes_all_topics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    broker_client = FakeBrokerClient()
    factory = FakeClientFactory(broker_client)
    monkeypatch.setattr("smart_desk.modules.mqtt.client.aiomqtt.Client", factory)
    task_manager = TaskManager()
    client = MqttClient(mqtt_settings(), task_manager)

    async def handler(_message: MqttMessage) -> None:
        return None

    client.register_handler("/smartdesk/one", handler, qos=1)
    client.register_handler("/smartdesk/two", handler, qos=0)

    await client.start()

    assert client.is_connected() is True
    assert broker_client.subscriptions == [
        ("/smartdesk/one", 1, 0.2),
        ("/smartdesk/two", 0, 0.2),
    ]
    assert factory.calls[0]["protocol"] is aiomqtt.ProtocolVersion.V311
    assert factory.calls[0]["clean_session"] is True
    assert factory.calls[0]["identifier"] == "smart-desk-server"

    with pytest.raises(RuntimeError, match="시작 후"):
        client.register_handler("/smartdesk/late", handler)
    with pytest.raises(RuntimeError, match="이미 실행"):
        await client.start()

    await client.stop()
    await client.stop()
    assert broker_client.exited is True
    assert client.is_connected() is False
    await task_manager.shutdown()


@pytest.mark.parametrize(
    "failed_client",
    [
        FakeBrokerClient(enter_error=aiomqtt.MqttError("connect failed")),
        FakeBrokerClient(subscribe_error=aiomqtt.MqttError("subscribe failed")),
    ],
)
async def test_cold_start_failure_reconnects_resubscribes_and_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    failed_client: FakeBrokerClient,
) -> None:
    connected_client = FakeBrokerClient()
    monkeypatch.setattr(
        "smart_desk.modules.mqtt.client.aiomqtt.Client",
        FakeClientFactory(failed_client, connected_client),
    )
    task_manager = TaskManager()
    client = MqttClient(mqtt_settings(reconnect_interval_seconds=0.05), task_manager)
    client.register_handler("/smartdesk/test", _ignore_message)

    await client.start()

    assert client.is_connected() is False
    with pytest.raises(MqttUnavailableError):
        await client.publish("/desk_ctl", '{"command":"STOP"}')

    await wait_until(lambda: connected_client.entered and client.is_connected())
    assert connected_client.subscriptions == [("/smartdesk/test", 1, 0.2)]
    assert task_manager.failures() == ()

    await client.stop()
    await task_manager.shutdown()


async def test_start_timeout_leaves_runner_alive_for_later_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    broker_client = FakeBrokerClient(enter_blocker=asyncio.Event())
    monkeypatch.setattr(
        "smart_desk.modules.mqtt.client.aiomqtt.Client",
        FakeClientFactory(broker_client),
    )
    task_manager = TaskManager()
    client = MqttClient(
        mqtt_settings(operation_timeout_seconds=0.01),
        task_manager,
    )

    await client.start()

    assert client.is_connected() is False
    broker_client.enter_blocker.set()
    await wait_until(lambda: broker_client.entered and client.is_connected())

    await client.stop()
    await task_manager.shutdown()


async def test_unexpected_initial_error_is_startup_error_and_critical_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "smart_desk.modules.mqtt.client.aiomqtt.Client",
        FakeClientFactory(FakeBrokerClient(enter_error=ValueError("bad settings"))),
    )
    task_manager = TaskManager()
    client = MqttClient(mqtt_settings(), task_manager)

    with pytest.raises(MqttStartupError, match="실패") as error:
        await client.start()

    assert isinstance(error.value.__cause__, ValueError)
    await wait_until(lambda: len(task_manager.failures()) == 1)
    failure = task_manager.failures()[0]
    assert failure.name == "mqtt"
    assert failure.critical is True
    assert isinstance(failure.error, ValueError)
    await task_manager.shutdown()


async def test_unexpected_runtime_error_ends_critical_runner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    broker_client = FakeBrokerClient()
    factory = FakeClientFactory(broker_client)
    monkeypatch.setattr("smart_desk.modules.mqtt.client.aiomqtt.Client", factory)
    task_manager = TaskManager()
    client = MqttClient(mqtt_settings(reconnect_interval_seconds=0.05), task_manager)
    await client.start()

    broker_client.messages.put(ValueError("programming error"))
    await wait_until(lambda: len(task_manager.failures()) == 1)

    assert client.is_connected() is False
    assert len(factory.calls) == 1
    failure = task_manager.failures()[0]
    assert failure.name == "mqtt"
    assert failure.critical is True
    assert isinstance(failure.error, ValueError)
    await client.stop()
    await task_manager.shutdown()


async def test_received_messages_are_converted_and_handler_errors_do_not_stop_runner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    broker_client = FakeBrokerClient()
    monkeypatch.setattr(
        "smart_desk.modules.mqtt.client.aiomqtt.Client",
        FakeClientFactory(broker_client),
    )
    task_manager = TaskManager()
    client = MqttClient(mqtt_settings(), task_manager)
    received: list[MqttMessage] = []
    completed = asyncio.Event()

    async def handler(message: MqttMessage) -> None:
        received.append(message)
        if len(received) == 1:
            raise ValueError("invalid payload")
        completed.set()

    client.register_handler("/smartdesk/test", handler)
    await client.start()
    broker_client.messages.put(
        mqtt_message("/smartdesk/test", b"first", retain=True)
    )
    broker_client.messages.put(
        mqtt_message("/smartdesk/test", b"second", qos=0)
    )

    async with asyncio.timeout(0.5):
        await completed.wait()

    assert [message.payload for message in received] == [b"first", b"second"]
    assert received[0].retained is True
    assert received[0].received_at.tzinfo is UTC
    assert received[1].qos == 0
    assert client.is_connected() is True

    await client.stop()
    await task_manager.shutdown()


async def test_publish_forwards_values_and_fails_when_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    broker_client = FakeBrokerClient()
    monkeypatch.setattr(
        "smart_desk.modules.mqtt.client.aiomqtt.Client",
        FakeClientFactory(broker_client),
    )
    task_manager = TaskManager()
    client = MqttClient(mqtt_settings(), task_manager)

    with pytest.raises(MqttUnavailableError):
        await client.publish("/desk_ctl", '{"command":"STOP"}')

    await client.start()
    await client.publish(
        "/desk_ctl",
        '{"command":"STOP"}',
        qos=1,
        retain=False,
    )

    assert broker_client.publications == [
        {
            "topic": "/desk_ctl",
            "payload": '{"command":"STOP"}',
            "qos": 1,
            "retain": False,
            "timeout": 0.2,
        }
    ]

    await client.stop()
    await task_manager.shutdown()


async def test_publish_error_is_converted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    broker_client = FakeBrokerClient(
        publish_error=aiomqtt.MqttError("publish failed")
    )
    monkeypatch.setattr(
        "smart_desk.modules.mqtt.client.aiomqtt.Client",
        FakeClientFactory(broker_client),
    )
    task_manager = TaskManager()
    client = MqttClient(mqtt_settings(), task_manager)
    await client.start()

    with pytest.raises(MqttUnavailableError):
        await client.publish("/smartdesk/test", b"payload")

    await client.stop()
    await task_manager.shutdown()


async def test_disconnect_reconnects_and_resubscribes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = FakeBrokerClient()
    second = FakeBrokerClient()
    factory = FakeClientFactory(first, second)
    monkeypatch.setattr("smart_desk.modules.mqtt.client.aiomqtt.Client", factory)
    task_manager = TaskManager()
    client = MqttClient(mqtt_settings(), task_manager)
    received: list[bytes] = []

    async def handler(message: MqttMessage) -> None:
        received.append(message.payload)

    client.register_handler("/smartdesk/test", handler, qos=1)
    await client.start()

    first.messages.put(aiomqtt.MqttError("connection lost"))
    await wait_until(lambda: second.entered and client.is_connected())
    second.messages.put(mqtt_message("/smartdesk/test", b"after-reconnect"))
    await wait_until(lambda: received == [b"after-reconnect"])

    assert first.exited is True
    assert first.subscriptions == [("/smartdesk/test", 1, 0.2)]
    assert second.subscriptions == [("/smartdesk/test", 1, 0.2)]
    assert len(factory.calls) == 2
    assert received == [b"after-reconnect"]

    await client.stop()
    await task_manager.shutdown()


async def _ignore_message(_message: MqttMessage) -> None:
    return None
