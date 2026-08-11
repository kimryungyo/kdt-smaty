"""MQTT transport가 기능 모듈에 전달하는 공개 타입."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, TypeAlias


MqttQos = Literal[0, 1, 2]


@dataclass(frozen=True, slots=True)
class MqttMessage:
    """라이브러리 타입에 의존하지 않는 수신 MQTT 메시지."""

    topic: str
    payload: bytes
    qos: MqttQos
    retained: bool
    received_at: datetime


MessageHandler: TypeAlias = Callable[[MqttMessage], Awaitable[None]]
