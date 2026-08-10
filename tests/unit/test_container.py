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
from smart_desk.modules.dashboard import get_dashboard
from smart_desk.modules.desk import get_desk
from smart_desk.modules.mqtt.topics import ESP32_STATUS_TOPIC
from smart_desk.modules.profiles import get_profiles


def test_get_container_requires_installation() -> None:
    with pytest.raises(ContainerNotInitializedError):
        get_container()


def test_installed_container_is_returned_as_same_instance() -> None:
    container = build_container(Settings(_env_file=None))
    install_container(container)

    assert get_container() is container
    assert get_mqtt() is container.mqtt
    assert get_desk() is container.desk
    assert get_profiles() is container.profiles
    assert get_dashboard() is container.dashboard


def test_build_container_assembles_desk_io_once_before_mqtt_start() -> None:
    container = build_container(Settings(_env_file=None))

    assert container.height_monitor is not None
    assert container.relay is not None
    assert container.desk is not None
    assert container.database is not None
    assert container.profiles is not None
    assert container.dashboard is not None
    assert [registration.name for registration in container.resources] == [
        "sqlite",
        "mqtt",
        "desk-height-monitor",
        "desk-controller",
    ]
    assert [registration.startup_order for registration in container.resources] == [
        5,
        10,
        20,
        30,
    ]
    assert [registration.shutdown_order for registration in container.resources] == [
        5,
        10,
        20,
        30,
    ]

    qos, handler = container.mqtt._handlers[ESP32_STATUS_TOPIC]  # noqa: SLF001
    assert qos == 0
    assert handler.__self__ is container.relay


def test_build_container_registers_media_only_when_enabled() -> None:
    disabled = build_container(Settings(_env_file=None))
    enabled = build_container(Settings(vision={"enabled": True}, _env_file=None))

    assert [registration.name for registration in disabled.resources] == [
        "sqlite",
        "mqtt",
        "desk-height-monitor",
        "desk-controller",
    ]
    assert [registration.name for registration in enabled.resources] == [
        "sqlite",
        "mqtt",
        "desk-height-monitor",
        "desk-controller",
        "camera-publisher-user",
        "camera-publisher-posture",
        "rtsp-frame-source-user",
        "rtsp-frame-source-posture",
    ]
    assert [registration.startup_order for registration in enabled.resources][-4:] == [
        40,
        41,
        50,
        51,
    ]
    assert [registration.shutdown_order for registration in enabled.resources][-4:] == [
        40,
        41,
        50,
        51,
    ]
    assert [
        registration.name
        for registration in sorted(
            enabled.resources,
            key=lambda registration: registration.shutdown_order,
            reverse=True,
        )
    ][:4] == [
        "rtsp-frame-source-posture",
        "rtsp-frame-source-user",
        "camera-publisher-posture",
        "camera-publisher-user",
    ]


def test_container_cannot_be_installed_twice() -> None:
    first = build_container(Settings(_env_file=None))
    second = build_container(Settings(_env_file=None))
    install_container(first)

    with pytest.raises(ContainerAlreadyInitializedError):
        install_container(second)
