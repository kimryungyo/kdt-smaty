"""AppContainer singleton 설치와 조회 테스트."""

import pytest

from smart_desk.bootstrap import build_container
from smart_desk.config.settings import Settings
from smart_desk.core.container import get_container, install_container
from smart_desk.core.exceptions import (
    ContainerAlreadyInitializedError,
    ContainerNotInitializedError,
)
from smart_desk.modules.mqtt import get_mqtt
from smart_desk.modules.desk import get_desk
from smart_desk.modules.mqtt.topics import ESP32_STATUS_TOPIC


def test_get_container_requires_installation() -> None:
    with pytest.raises(ContainerNotInitializedError):
        get_container()


def test_installed_container_is_returned_as_same_instance() -> None:
    container = build_container(Settings(_env_file=None))
    install_container(container)

    assert get_container() is container
    assert get_mqtt() is container.mqtt
    assert get_desk() is container.desk


def test_build_container_assembles_desk_io_once_before_mqtt_start() -> None:
    container = build_container(Settings(_env_file=None))

    assert container.height_monitor is not None
    assert container.relay is not None
    assert container.desk is not None
    assert [registration.name for registration in container.resources] == [
        "mqtt",
        "desk-height-monitor",
        "desk-controller",
    ]
    assert [registration.startup_order for registration in container.resources] == [
        10,
        20,
        30,
    ]
    assert [registration.shutdown_order for registration in container.resources] == [
        10,
        20,
        30,
    ]

    qos, handler = container.mqtt._handlers[ESP32_STATUS_TOPIC]  # noqa: SLF001
    assert qos == 0
    assert handler.__self__ is container.relay


def test_container_cannot_be_installed_twice() -> None:
    first = build_container(Settings(_env_file=None))
    second = build_container(Settings(_env_file=None))
    install_container(first)

    with pytest.raises(ContainerAlreadyInitializedError):
        install_container(second)
