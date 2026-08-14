"""AppContainer singleton 설치와 조회 테스트."""

import subprocess
import sys
from types import SimpleNamespace

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
    assert container.assistant is None
    assert container.voice is None
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


def test_build_container_registers_media_roles_independently() -> None:
    disabled = build_container(Settings(_env_file=None))
    split = build_container(
        Settings(
            media={
                "user": {"receive_enabled": True},
                "posture": {"publish_enabled": True},
            },
            _env_file=None,
        )
    )

    assert [registration.name for registration in disabled.resources] == [
        "sqlite",
        "mqtt",
        "desk-height-monitor",
        "desk-controller",
    ]
    assert disabled.user_camera_publisher is None
    assert disabled.posture_camera_publisher is None
    assert disabled.user_frame_source is None
    assert disabled.posture_frame_source is None
    assert [registration.name for registration in split.resources] == [
        "sqlite",
        "mqtt",
        "desk-height-monitor",
        "desk-controller",
        "camera-publisher-posture",
        "rtsp-frame-source-user",
    ]
    assert split.user_camera_publisher is None
    assert split.posture_camera_publisher is not None
    assert split.user_frame_source is not None
    assert split.posture_frame_source is None


def test_build_container_preserves_media_startup_and_shutdown_order() -> None:
    enabled = build_container(
        Settings(
            media={
                "user": {"publish_enabled": True, "receive_enabled": True},
                "posture": {"publish_enabled": True, "receive_enabled": True},
            },
            _env_file=None,
        )
    )

    assert [registration.startup_order for registration in enabled.resources][-4:] == [
        40, 41, 50, 51
    ]
    assert [registration.shutdown_order for registration in enabled.resources][-4:] == [
        40, 41, 50, 51
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


def test_build_container_registers_voice_at_order_70_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = SimpleNamespace()
    monkeypatch.setitem(
        sys.modules,
        "openai",
        SimpleNamespace(AsyncOpenAI=lambda **_kwargs: fake_client),
    )
    settings = Settings(
        voice={"enabled": True},
        openai={"api_key": "test-key"},
        _env_file=None,
    )

    container = build_container(settings)

    assert container.assistant is not None
    assert container.voice is not None
    assert container.resources[-1].name == "voice"
    assert container.resources[-1].startup_order == 70
    assert container.resources[-1].shutdown_order == 70


def test_build_container_registers_voice_debug_after_voice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = SimpleNamespace()
    monkeypatch.setitem(
        sys.modules,
        "openai",
        SimpleNamespace(AsyncOpenAI=lambda **_kwargs: fake_client),
    )
    settings = Settings(
        voice={"enabled": True},
        voice_debug={"enabled": True},
        openai={"api_key": "test-key"},
        _env_file=None,
    )

    container = build_container(settings)

    assert container.voice_debug is not None
    assert [resource.name for resource in container.resources[-2:]] == [
        "voice",
        "voice-debug-http",
    ]
    assert container.resources[-1].startup_order == 80
    assert container.resources[-1].shutdown_order == 80


def test_disabled_voice_does_not_import_optional_packages() -> None:
    code = """
import builtins
real_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name.split('.')[0] in {'openai', 'sounddevice', 'livekit'}:
        raise AssertionError(f'unexpected optional import: {name}')
    return real_import(name, *args, **kwargs)
builtins.__import__ = guarded_import
from smart_desk.bootstrap import build_container
from smart_desk.config.settings import Settings
container = build_container(Settings(_env_file=None))
assert container.voice is None
"""

    result = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_container_cannot_be_installed_twice() -> None:
    first = build_container(Settings(_env_file=None))
    second = build_container(Settings(_env_file=None))
    install_container(first)

    with pytest.raises(ContainerAlreadyInitializedError):
        install_container(second)
