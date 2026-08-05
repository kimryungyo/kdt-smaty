"""애플리케이션의 단일 MQTT client와 공개 타입."""

from smart_desk.core.container import get_container
from smart_desk.modules.mqtt.client import (
    MqttClient,
    MqttClientError,
    MqttStartupError,
    MqttUnavailableError,
)
from smart_desk.modules.mqtt.models import MessageHandler, MqttMessage, MqttQos


def get_mqtt() -> MqttClient:
    """AppContainer가 소유한 프로세스 공용 MQTT client를 반환한다."""

    return get_container().mqtt


__all__ = [
    "MessageHandler",
    "MqttClient",
    "MqttClientError",
    "MqttMessage",
    "MqttQos",
    "MqttStartupError",
    "MqttUnavailableError",
    "get_mqtt",
]
