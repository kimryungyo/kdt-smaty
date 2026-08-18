"""RelayClient의 ESP32 상태 수신과 명령 발행 계약 테스트."""

from __future__ import annotations

from datetime import UTC, datetime
import json
from typing import Any

import pytest
from pydantic import ValidationError

from smart_desk.modules.desk.models import (
    Direction,
    RelayEvent,
    RelayState,
)
from smart_desk.modules.desk.relay import RelayClient
from smart_desk.modules.desk.messages import RelayWakeMessage
from smart_desk.modules.mqtt.client import MqttUnavailableError
from smart_desk.modules.mqtt.models import MqttMessage
from smart_desk.modules.mqtt.topics import ESP32_COMMAND_TOPIC, ESP32_STATUS_TOPIC


class FakeMqttClient:
    """relay publish 호출을 기록하거나 지정 오류를 발생시킨다."""

    def __init__(self, *, error: BaseException | None = None) -> None:
        self.error = error
        self.publications: list[dict[str, Any]] = []

    async def publish(
        self,
        topic: str,
        payload: bytes | str,
        *,
        qos: int,
        retain: bool,
    ) -> None:
        if self.error is not None:
            raise self.error
        self.publications.append(
            {"topic": topic, "payload": payload, "qos": qos, "retain": retain}
        )


def mqtt_message(
    payload: bytes | str,
    *,
    retained: bool = False,
    received_at: datetime | None = None,
) -> MqttMessage:
    return MqttMessage(
        topic=ESP32_STATUS_TOPIC,
        payload=payload.encode() if isinstance(payload, str) else payload,
        qos=0,
        retained=retained,
        received_at=received_at or datetime(2026, 8, 6, 5, 0, tzinfo=UTC),
    )


@pytest.mark.parametrize(
    ("event", "state"),
    [
        ("online", RelayState.STOP),
        ("heartbeat", RelayState.STOP),
        ("moving", RelayState.UP),
        ("stopped", RelayState.STOP),
        ("rejected", RelayState.DOWN),
    ],
)
async def test_live_status_events_update_snapshot(event: str, state: RelayState) -> None:
    received_at = datetime(2026, 8, 6, 5, 1, tzinfo=UTC)
    relay = RelayClient(FakeMqttClient())  # type: ignore[arg-type]
    payload = json.dumps(
        {
            "event": event,
            "state": state.value,
            "firmware": " smartdesk-relay-1.0.5 ",
            "code": " command ",
            "detail": " 정상 ",
            "future_field": 1,
        }
    )

    await relay.handle_status(mqtt_message(payload, received_at=received_at))

    snapshot = relay.get_snapshot()
    assert snapshot.event is RelayEvent(event)
    assert snapshot.state is state
    assert snapshot.firmware == "smartdesk-relay-1.0.5"
    assert snapshot.code == "command"
    assert snapshot.detail == "정상"
    assert snapshot.received_at is received_at
    assert snapshot.last_error is None


async def test_offline_will_can_omit_firmware() -> None:
    relay = RelayClient(FakeMqttClient())  # type: ignore[arg-type]

    await relay.handle_status(mqtt_message('{"event":"offline","state":"STOP"}'))

    snapshot = relay.get_snapshot()
    assert snapshot.event is RelayEvent.OFFLINE
    assert snapshot.state is RelayState.STOP
    assert snapshot.firmware is None


@pytest.mark.parametrize(
    ("event", "state", "code"),
    [
        ("online", "STOP", "height_waiting"),
        ("online", "STOP", "ready"),
        ("moving", "UP", "command_started"),
        ("moving", "DOWN", "deadline_extended"),
        ("stopped", "STOP", "command"),
        ("stopped", "STOP", "timeout"),
        ("rejected", "STOP", "height_not_ready"),
        ("rejected", "STOP", "upper_limit"),
    ],
)
async def test_fin_firmware_status_contract_is_accepted(
    event: str,
    state: str,
    code: str,
) -> None:
    relay = RelayClient(FakeMqttClient())  # type: ignore[arg-type]
    payload = json.dumps(
        {
            "event": event,
            "state": state,
            "firmware": "smartdesk-fin-relay-1.0.0",
            "code": code,
            "detail": "계약 테스트",
        }
    )

    await relay.handle_status(mqtt_message(payload))

    assert relay.get_snapshot().firmware == "smartdesk-fin-relay-1.0.0"
    assert relay.get_snapshot().code == code
    assert relay.get_snapshot().last_error is None


@pytest.mark.parametrize(
    "payload",
    [
        b"{",
        b"[]",
        b'{"event":"unknown","state":"STOP","firmware":"v1"}',
        b'{"event":"online","state":"INVALID","firmware":"v1"}',
        b'{"event":"online","state":"STOP"}',
        b'{"event":"online","state":"STOP","firmware":""}',
        b'{"event":"online","state":"STOP","firmware":123}',
        b'{"event":"online","state":"STOP","firmware":"v1","code":" "}',
    ],
)
async def test_invalid_status_preserves_last_valid_snapshot(payload: bytes) -> None:
    first_received = datetime(2026, 8, 6, 5, 0, tzinfo=UTC)
    relay = RelayClient(FakeMqttClient())  # type: ignore[arg-type]
    await relay.handle_status(
        mqtt_message(
            '{"event":"moving","state":"UP","firmware":"v1"}',
            received_at=first_received,
        )
    )

    await relay.handle_status(
        mqtt_message(payload, received_at=first_received.replace(minute=2))
    )

    snapshot = relay.get_snapshot()
    assert snapshot.event is RelayEvent.MOVING
    assert snapshot.state is RelayState.UP
    assert snapshot.firmware == "v1"
    assert snapshot.received_at is first_received
    assert snapshot.last_error

    await relay.handle_status(
        mqtt_message('{"event":"heartbeat","state":"STOP","firmware":"v1"}')
    )
    assert relay.get_snapshot().last_error is None


async def test_retained_status_never_changes_snapshot() -> None:
    relay = RelayClient(FakeMqttClient())  # type: ignore[arg-type]
    original = relay.get_snapshot()

    await relay.handle_status(
        mqtt_message(
            '{"event":"online","state":"STOP","firmware":"v1"}',
            retained=True,
        )
    )
    await relay.handle_status(mqtt_message(b"{", retained=True))

    assert relay.get_snapshot() is original


@pytest.mark.parametrize(
    ("direction", "hold_ms"),
    [(Direction.UP, 50), (Direction.DOWN, 500)],
)
async def test_pulse_publishes_exact_contract(
    direction: Direction,
    hold_ms: int,
) -> None:
    mqtt = FakeMqttClient()
    relay = RelayClient(mqtt)  # type: ignore[arg-type]

    await relay.pulse(direction, hold_ms)

    assert len(mqtt.publications) == 1
    publication = mqtt.publications[0]
    assert publication["topic"] == ESP32_COMMAND_TOPIC
    assert publication["qos"] == 0
    assert publication["retain"] is False
    assert json.loads(str(publication["payload"])) == {
        "command": direction.value,
        "source": "desk_service",
        "hold_ms": hold_ms,
    }


@pytest.mark.parametrize("hold_ms", [True, 50.0, "50", None])
async def test_pulse_rejects_non_integer_hold(hold_ms: object) -> None:
    mqtt = FakeMqttClient()
    relay = RelayClient(mqtt)  # type: ignore[arg-type]

    with pytest.raises(TypeError, match="정수"):
        await relay.pulse(Direction.UP, hold_ms)  # type: ignore[arg-type]
    assert mqtt.publications == []


@pytest.mark.parametrize("hold_ms", [49, 501])
async def test_pulse_rejects_out_of_range_hold(hold_ms: int) -> None:
    mqtt = FakeMqttClient()
    relay = RelayClient(mqtt)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="50~500"):
        await relay.pulse(Direction.UP, hold_ms)
    assert mqtt.publications == []


@pytest.mark.parametrize("direction", ["UP", RelayState.UP, None])
async def test_pulse_rejects_non_direction_values(direction: object) -> None:
    mqtt = FakeMqttClient()
    relay = RelayClient(mqtt)  # type: ignore[arg-type]

    with pytest.raises(TypeError, match="Direction"):
        await relay.pulse(direction, 100)  # type: ignore[arg-type]
    assert mqtt.publications == []


async def test_send_stop_uses_exact_json_and_does_not_change_snapshot() -> None:
    mqtt = FakeMqttClient()
    relay = RelayClient(mqtt)  # type: ignore[arg-type]
    original = relay.get_snapshot()

    await relay.send_stop()

    assert mqtt.publications == [
        {
            "topic": ESP32_COMMAND_TOPIC,
            "payload": '{"command":"STOP"}',
            "qos": 0,
            "retain": False,
        }
    ]
    assert relay.get_snapshot() is original


async def test_wake_publishes_exact_contract() -> None:
    mqtt = FakeMqttClient()
    relay = RelayClient(mqtt)  # type: ignore[arg-type]

    await relay.wake(Direction.DOWN, 80.2)

    assert mqtt.publications == [
        {
            "topic": ESP32_COMMAND_TOPIC,
            "payload": (
                '{"command":"WAKE","source":"desk_service","direction":"DOWN",'
                '"hold_ms":400,"basis_height_cm":80.2}'
            ),
            "qos": 0,
            "retain": False,
        }
    ]


@pytest.mark.parametrize("basis", [True, 72.9, 118.1, float("inf")])
async def test_wake_rejects_invalid_basis_height(basis: object) -> None:
    mqtt = FakeMqttClient()
    relay = RelayClient(mqtt)  # type: ignore[arg-type]

    with pytest.raises((TypeError, ValueError)):
        await relay.wake(Direction.UP, basis)  # type: ignore[arg-type]
    assert mqtt.publications == []


@pytest.mark.parametrize(
    "payload",
    [
        {"command": "WAKE", "source": "desk_service", "direction": "UP", "hold_ms": 100, "basis_height_cm": 80.0},
        {"command": "WAKE", "source": "desk_service", "direction": "SIDEWAYS", "hold_ms": 400, "basis_height_cm": 80.0},
        {"command": "WAKE", "source": "desk_service", "direction": "UP", "hold_ms": 400, "basis_height_cm": 80.0, "extra": True},
    ],
)
def test_wake_wire_model_rejects_non_exact_contract(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        RelayWakeMessage.model_validate(payload)


@pytest.mark.parametrize("method", ["pulse", "stop"])
async def test_command_publish_error_is_propagated(method: str) -> None:
    mqtt = FakeMqttClient(error=MqttUnavailableError("offline"))
    relay = RelayClient(mqtt)  # type: ignore[arg-type]

    with pytest.raises(MqttUnavailableError, match="offline"):
        if method == "pulse":
            await relay.pulse(Direction.DOWN, 100)
        else:
            await relay.send_stop()
