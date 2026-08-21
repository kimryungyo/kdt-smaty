"""AppContainer singleton 설치와 조회 테스트."""

import subprocess
import sys
from types import SimpleNamespace

import pytest

import smart_desk.bootstrap as bootstrap_module
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
from smart_desk.modules.profiles import get_activity_modes, get_profiles
from smart_desk.modules.assistant.realtime_runtime import RealtimeVoiceRuntime
from smart_desk.modules.identity import UnavailableFaceEmbeddingExtractor
from smart_desk.modules.vision import CompositeVisionDetector, NoopVisionDetector


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
    assert get_activity_modes() is container.activity_modes
    assert get_dashboard() is container.dashboard


def test_build_container_assembles_desk_io_once_before_mqtt_start() -> None:
    container = build_container(Settings(_env_file=None))

    assert container.height_monitor is not None
    assert container.relay is not None
    assert container.desk is not None
    assert container.database is not None
    assert container.profiles is not None
    assert container.activity_modes is not None
    assert container.dashboard is not None
    assert container.face_embeddings is not None
    assert container.current_user is not None
    assert container.identity is not None
    assert container.voice is None
    assert [registration.name for registration in container.resources] == [
        "sqlite",
        "mqtt",
        "desk-height-monitor",
        "desk-controller",
        "vision",
        "face-identity",
        "profile-memory",
        "recent-user",
        "assistant-context",
        "assistant-turns",
        "desk-automation",
    ]
    assert [registration.startup_order for registration in container.resources] == [
        5, 10, 20, 30, 60, 70, 72, 74, 75, 76, 80,
    ]
    assert [registration.shutdown_order for registration in container.resources] == [
        5, 10, 20, 30, 60, 70, 72, 74, 75, 76, 80,
    ]

    qos, handler = container.mqtt._handlers[ESP32_STATUS_TOPIC]  # noqa: SLF001
    assert qos == 0
    assert handler.__self__ is container.relay


def test_missing_lower_pose_model_falls_back_to_noop_without_failing_bootstrap() -> None:
    container = build_container(
        Settings(vision={"lower_pose_model_path": "/missing/yolo26n-pose.onnx"}, _env_file=None)
    )
    assert container.vision is not None
    assert isinstance(container.vision._detector, NoopVisionDetector)  # noqa: SLF001


def test_face_detector_and_embedding_fail_closed_independently(monkeypatch) -> None:
    class Extractor:
        model_name = "test"
        model_version = "1"
        dimension = 128
        normalization = "l2"

        def __init__(self, *_args, **_kwargs): pass
        def extract(self, _observation): return None

    monkeypatch.setattr(
        bootstrap_module,
        "OpenCvYuNetUpperDetector",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("bad detector")),
    )
    monkeypatch.setattr(bootstrap_module, "OpenCvSFaceEmbeddingExtractor", Extractor)
    detector_failed = build_container(Settings(face={
        "detector_model_path": "/tmp/yunet.onnx",
        "embedding_model_path": "/tmp/sface.onnx",
    }, _env_file=None))
    assert isinstance(detector_failed.vision._detector, NoopVisionDetector)  # noqa: SLF001
    assert isinstance(detector_failed.identity._extractor, Extractor)  # noqa: SLF001

    class Upper:
        def __init__(self, *_args, **_kwargs): pass
        def detect_upper(self, _frame): return None
        def detect_lower(self, _frame): return None

    monkeypatch.setattr(bootstrap_module, "OpenCvYuNetUpperDetector", Upper)
    monkeypatch.setattr(
        bootstrap_module,
        "OpenCvSFaceEmbeddingExtractor",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("bad embedding")),
    )
    embedding_failed = build_container(Settings(face={
        "detector_model_path": "/tmp/yunet.onnx",
        "embedding_model_path": "/tmp/sface.onnx",
    }, _env_file=None))
    assert isinstance(embedding_failed.vision._detector, Upper)  # noqa: SLF001
    assert isinstance(  # noqa: SLF001
        embedding_failed.identity._extractor, UnavailableFaceEmbeddingExtractor
    )


def test_bootstrap_composes_configured_upper_and_lower_detectors(monkeypatch) -> None:
    class Upper:
        def __init__(self, *_args, **_kwargs): pass
        def detect_upper(self, _frame): return None
        def detect_lower(self, _frame): return None

    class Lower(Upper):
        pass

    monkeypatch.setattr(bootstrap_module, "OpenCvYuNetUpperDetector", Upper)
    monkeypatch.setattr(bootstrap_module, "OpenCvYoloPoseLowerDetector", Lower)
    container = build_container(Settings(
        face={"detector_model_path": "/tmp/yunet.onnx"},
        vision={"lower_pose_model_path": "/tmp/pose.onnx"},
        _env_file=None,
    ))
    assert isinstance(container.vision._detector, CompositeVisionDetector)  # noqa: SLF001


def test_build_container_registers_media_roles_independently() -> None:
    disabled = build_container(Settings(_env_file=None))
    split = build_container(
        Settings(
            media={
                "user": {"receive_enabled": True},
                "workspace": {"enabled": True},
            },
            _env_file=None,
        )
    )

    assert [registration.name for registration in disabled.resources] == [
        "sqlite",
        "mqtt",
        "desk-height-monitor",
        "desk-controller",
        "vision",
        "face-identity",
        "profile-memory",
        "recent-user",
        "assistant-context",
        "assistant-turns",
        "desk-automation",
    ]
    assert disabled.user_camera_publisher is None
    assert disabled.workspace_camera is None
    assert disabled.posture_camera_publisher is None
    assert disabled.user_frame_source is None
    assert disabled.posture_frame_source is None
    assert [registration.name for registration in split.resources] == [
        "sqlite",
        "mqtt",
        "desk-height-monitor",
        "desk-controller",
        "workspace-camera",
        "webrtc-frame-source-user",
        "vision",
        "face-identity",
        "profile-memory",
        "recent-user",
        "assistant-context",
        "assistant-turns",
        "desk-automation",
    ]
    assert split.user_camera_publisher is None
    assert split.workspace_camera is not None
    assert split.posture_camera_publisher is None
    assert split.user_frame_source is not None
    assert split.posture_frame_source is None


def test_build_container_preserves_media_startup_and_shutdown_order() -> None:
    enabled = build_container(
        Settings(
            media={
                "user": {"publish_enabled": True, "receive_enabled": True},
                "posture": {"receive_enabled": True},
                "workspace": {"enabled": True},
            },
            _env_file=None,
        )
    )

    media_resources = [
        registration
        for registration in enabled.resources
        if registration.name.startswith("webrtc-")
        or registration.name == "workspace-camera"
    ]
    assert [
        (registration.name, registration.startup_order, registration.shutdown_order)
        for registration in media_resources
    ] == [
        ("webrtc-camera-publisher-user", 40, 40),
        ("workspace-camera", 42, 42),
        ("webrtc-frame-source-user", 50, 50),
        ("webrtc-frame-source-posture", 51, 51),
    ]
    assert [
        registration.name
        for registration in sorted(
            enabled.resources,
            key=lambda registration: registration.shutdown_order,
            reverse=True,
        )
    ][:11] == [
        "desk-automation",
        "assistant-turns",
        "assistant-context",
        "recent-user",
        "profile-memory",
        "face-identity",
        "vision",
        "webrtc-frame-source-posture",
        "webrtc-frame-source-user",
        "workspace-camera",
        "webrtc-camera-publisher-user",
    ]


def test_build_container_registers_voice_at_order_90_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Runtime:
        async def stop(self): pass
        async def run_audio(self, _):
            if False: yield None
    received: dict[str, object] = {}

    def build_runtime(cls, **kwargs):
        received.update(kwargs)
        return Runtime()

    monkeypatch.setattr(RealtimeVoiceRuntime, "build_for_services", classmethod(build_runtime))
    settings = Settings(
        voice={"enabled": True},
        openai={"api_key": "test-key", "realtime_model": "configured-realtime-model"},
        _env_file=None,
    )

    container = build_container(settings)

    assert container.voice is not None
    voice_resources = {resource.name: resource for resource in container.resources}
    assert voice_resources["voice"].startup_order == 90
    assert voice_resources["voice"].shutdown_order == 90
    assert voice_resources["voice-speech-synthesizer"].shutdown_order == 91
    assert voice_resources["voice-announcer"].shutdown_order == 92
    assert voice_resources["voice-greeting"].shutdown_order == 93
    config = received["config"]
    assert config.model == "configured-realtime-model"  # type: ignore[union-attr]
    assert config.input_transcription_model == "gpt-transcribe"  # type: ignore[union-attr]
    assert config.reasoning_effort == "medium"  # type: ignore[union-attr]
    assert received["workspace_camera"] is None
    assert received["workspace_frame_freshness_seconds"] == 2.0


def test_build_container_registers_voice_debug_after_voice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Runtime:
        async def stop(self): pass
        async def run_audio(self, _):
            if False: yield None
    monkeypatch.setattr(RealtimeVoiceRuntime, "build_for_services", classmethod(lambda cls, **_: Runtime()))
    settings = Settings(
        voice={"enabled": True},
        voice_debug={"enabled": True},
        openai={"api_key": "test-key"},
        _env_file=None,
    )

    container = build_container(settings)

    assert container.voice_debug is not None
    assert [resource.name for resource in container.resources[-5:]] == [
        "voice",
        "voice-speech-synthesizer",
        "voice-announcer",
        "voice-greeting",
        "voice-debug-http",
    ]
    assert container.resources[-1].startup_order == 94
    assert container.resources[-1].shutdown_order == 94


@pytest.mark.parametrize("failure", [ImportError("agents missing"), ValueError("bad runtime config")])
def test_enabled_voice_build_failure_is_explicit_and_preserves_cause(
    monkeypatch: pytest.MonkeyPatch, failure: Exception,
) -> None:
    def fail_build(cls, **_):
        raise failure

    monkeypatch.setattr(RealtimeVoiceRuntime, "build_for_services", classmethod(fail_build))
    settings = Settings(
        voice={"enabled": True},
        openai={"api_key": "test-key"},
        _env_file=None,
    )

    with pytest.raises(RuntimeError, match="Voice resource 'voice'") as captured:
        build_container(settings)

    assert captured.value.__cause__ is failure


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
