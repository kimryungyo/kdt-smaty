"""환경변수 설정 검증 테스트."""

import pytest
from pydantic import ValidationError
from pathlib import Path

from smart_desk.config.settings import Settings, VisionSettings


def test_nested_environment_variable_is_loaded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMART_DESK_SERVER__PORT", "9191")

    settings = Settings(_env_file=None)

    assert settings.server.port == 9191


def test_automatic_movement_execution_defaults_off_and_reads_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert Settings(_env_file=None).automation.execute_automatic_movements is False

    monkeypatch.setenv("SMART_DESK_AUTOMATION__EXECUTE_AUTOMATIC_MOVEMENTS", "true")

    assert Settings(_env_file=None).automation.execute_automatic_movements is True


def test_mqtt_environment_variables_are_loaded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMART_DESK_MQTT__CLIENT_ID", "test-mqtt-client")
    monkeypatch.setenv("SMART_DESK_MQTT__OPERATION_TIMEOUT_SECONDS", "3.5")
    monkeypatch.setenv("SMART_DESK_MQTT__RECONNECT_INTERVAL_SECONDS", "1.5")

    settings = Settings(_env_file=None)

    assert settings.mqtt.client_id == "test-mqtt-client"
    assert settings.mqtt.operation_timeout_seconds == 3.5
    assert settings.mqtt.reconnect_interval_seconds == 1.5


def test_empty_mqtt_client_id_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMART_DESK_MQTT__CLIENT_ID", "")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_default_serial_and_height_staleness_settings() -> None:
    settings = Settings(_env_file=None)

    assert settings.serial.port == "/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0"
    assert settings.serial.baudrate == 115200
    assert settings.serial.read_timeout_seconds == 0.2
    assert settings.serial.reconnect_interval_seconds == 1.0
    assert settings.desk.height_stale_after_seconds == 1.0


def test_serial_environment_variables_are_loaded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SMART_DESK_SERIAL__PORT", "  /dev/test-desk  ")
    monkeypatch.setenv("SMART_DESK_SERIAL__BAUDRATE", "57600")
    monkeypatch.setenv("SMART_DESK_SERIAL__READ_TIMEOUT_SECONDS", "0.4")
    monkeypatch.setenv("SMART_DESK_SERIAL__RECONNECT_INTERVAL_SECONDS", "2.5")
    monkeypatch.setenv("SMART_DESK_DESK__HEIGHT_STALE_AFTER_SECONDS", "1.5")

    settings = Settings(_env_file=None)

    assert settings.serial.port == "/dev/test-desk"
    assert settings.serial.baudrate == 57600
    assert settings.serial.read_timeout_seconds == 0.4
    assert settings.serial.reconnect_interval_seconds == 2.5
    assert settings.desk.height_stale_after_seconds == 1.5


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("PORT", " "),
        ("BAUDRATE", "0"),
        ("READ_TIMEOUT_SECONDS", "0"),
        ("RECONNECT_INTERVAL_SECONDS", "0"),
    ],
)
def test_invalid_serial_settings_are_rejected(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    value: str,
) -> None:
    monkeypatch.setenv(f"SMART_DESK_SERIAL__{name}", value)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_boolean_baudrate_is_rejected() -> None:
    with pytest.raises(ValidationError, match="bool"):
        Settings(serial={"baudrate": True}, _env_file=None)


@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan")])
def test_invalid_height_staleness_is_rejected(value: float) -> None:
    with pytest.raises(ValidationError):
        Settings(desk={"height_stale_after_seconds": value}, _env_file=None)


def test_multiple_workers_are_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMART_DESK_SERVER__WORKERS", "2")

    with pytest.raises(ValidationError, match="worker를 하나만"):
        Settings(_env_file=None)


def test_operation_range_cannot_exceed_control_maximum(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SMART_DESK_DESK__OPERATION_MAX_CM", "116")

    with pytest.raises(ValidationError, match="제어 상한 115cm"):
        Settings(_env_file=None)


def test_operation_range_cannot_go_below_control_minimum(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SMART_DESK_DESK__OPERATION_MIN_CM", "74")

    with pytest.raises(ValidationError, match="제어 하한 75cm"):
        Settings(_env_file=None)


def test_measurement_range_cannot_exceed_physical_maximum(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SMART_DESK_DESK__MEASUREMENT_MAX_CM", "119")

    with pytest.raises(ValidationError, match="측정 최대 높이는 물리 상한 118cm"):
        Settings(_env_file=None)


def test_measurement_range_cannot_go_below_physical_minimum(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SMART_DESK_DESK__MEASUREMENT_MIN_CM", "72")

    with pytest.raises(ValidationError, match="물리 하한 73cm"):
        Settings(_env_file=None)


def test_default_desk_ranges_match_physical_and_control_limits() -> None:
    settings = Settings(_env_file=None)

    assert settings.desk.physical_min_cm == 73.0
    assert settings.desk.measurement_min_cm == 73.0
    assert settings.desk.physical_max_cm == 118.0
    assert settings.desk.measurement_max_cm == 118.0
    assert settings.desk.operation_min_cm == 75.0
    assert settings.desk.operation_max_cm == 115.0
    assert settings.desk.continuous_hold_ms == 500
    assert settings.desk.manual_hold_ms == 500
    assert settings.desk.fine_hold_ms == 350
    assert settings.desk.max_fine_pulses == 2
    assert settings.desk.target_tolerance_cm == 1.0
    assert settings.desk.pulse_refresh_interval_seconds == 0.1
    assert settings.desk.relay_ack_timeout_seconds == 6.0


def test_relay_ack_timeout_must_exceed_mqtt_publish_timeout() -> None:
    with pytest.raises(ValidationError, match="MQTT publish timeout"):
        Settings(
            mqtt={"operation_timeout_seconds": 5},
            desk={"relay_ack_timeout_seconds": 5},
            _env_file=None,
        )


@pytest.mark.parametrize(
    "desk",
    [
        {"continuous_hold_ms": True},
        {"pulse_refresh_interval_seconds": 0.5, "continuous_hold_ms": 500},
        {"control_poll_interval_seconds": 0.2},
        {"manual_watchdog_seconds": 0.01},
        {"target_tolerance_cm": 1.5, "fine_approach_distance_cm": 1.5},
        {"relay_stale_after_seconds": 1, "relay_ack_timeout_seconds": 1},
    ],
)
def test_invalid_desk_control_timing_is_rejected(desk: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Settings(desk=desk, _env_file=None)


def test_frontend_directory_can_be_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMART_DESK_DASHBOARD__FRONTEND_DIRECTORY", "custom-ui/dist")

    settings = Settings(_env_file=None)

    assert settings.dashboard.frontend_directory.as_posix() == "custom-ui/dist"


def test_default_storage_database_path() -> None:
    settings = Settings(_env_file=None)

    assert settings.storage.database_path == Path("data/smart_desk.db")


def test_lower_pose_settings_default_disabled_and_normalize_blank_path(monkeypatch: pytest.MonkeyPatch) -> None:
    assert VisionSettings().lower_pose_model_path is None
    assert VisionSettings().max_camera_skew_seconds == 0.75
    assert VisionSettings().frame_stale_after_seconds == 3.0
    assert VisionSettings().result_stale_after_seconds == 3.0
    assert VisionSettings().stability_majority_ratio == 0.7
    assert VisionSettings().stability_min_samples == 3
    assert VisionSettings().upper_presence_min_person_confidence == 0.60
    monkeypatch.setenv("SMART_DESK_VISION__LOWER_POSE_MODEL_PATH", "   ")
    monkeypatch.setenv("SMART_DESK_VISION__UPPER_INFERENCE_INTERVAL_SECONDS", "0.5")
    monkeypatch.setenv("SMART_DESK_VISION__LOWER_INFERENCE_INTERVAL_SECONDS", "0.5")
    settings = Settings(_env_file=None)
    assert settings.vision.lower_pose_model_path is None
    assert settings.vision.upper_inference_interval_seconds == 0.5
    assert settings.vision.lower_inference_interval_seconds == 0.5


@pytest.mark.parametrize("value", [0, -1, 0.49, 11])
def test_lower_pose_inference_interval_has_valid_range(value: float) -> None:
    with pytest.raises(ValidationError):
        VisionSettings(lower_inference_interval_seconds=value)


@pytest.mark.parametrize("value", [0, -1, 0.49, 11])
def test_upper_presence_inference_interval_has_valid_range(value: float) -> None:
    with pytest.raises(ValidationError):
        VisionSettings(upper_inference_interval_seconds=value)


@pytest.mark.parametrize("value", [0.5, 0.0, 1.1])
def test_vision_stability_majority_ratio_has_valid_range(value: float) -> None:
    with pytest.raises(ValidationError):
        VisionSettings(stability_majority_ratio=value)


@pytest.mark.parametrize("value", [0, 1, 101])
def test_vision_stability_min_samples_has_valid_range(value: int) -> None:
    with pytest.raises(ValidationError):
        VisionSettings(stability_min_samples=value)


def test_storage_database_path_is_loaded_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SMART_DESK_STORAGE__DATABASE_PATH", "/tmp/test-smart-desk.db")

    settings = Settings(_env_file=None)

    assert settings.storage.database_path == Path("/tmp/test-smart-desk.db")


def test_camera_media_settings_are_loaded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMART_DESK_MEDIA__USER__PUBLISH_ENABLED", "true")
    monkeypatch.setenv("SMART_DESK_MEDIA__USER__RECEIVE_ENABLED", "true")
    monkeypatch.setenv("SMART_DESK_MEDIA__USER__DEVICE", "  /dev/test-user  ")
    monkeypatch.setenv(
        "SMART_DESK_MEDIA__USER__PUBLISH_URL", "https://media/user/whip"
    )
    monkeypatch.setenv("SMART_DESK_MEDIA__USER__WIDTH", "640")
    monkeypatch.setenv("SMART_DESK_MEDIA__WORKSPACE__RECEIVE_ENABLED", "true")
    monkeypatch.setenv(
        "SMART_DESK_MEDIA__WORKSPACE__RECEIVE_URL",
        "https://media/workspace-cam/whep",
    )
    monkeypatch.setenv(
        "SMART_DESK_MEDIA__RECONNECT_INTERVAL_SECONDS", "2.5"
    )

    settings = Settings(_env_file=None)

    assert settings.media.user.publish_enabled is True
    assert settings.media.user.receive_enabled is True
    assert settings.media.user.device == "/dev/test-user"
    assert settings.media.user.publish_url == "https://media/user/whip"
    assert settings.media.user.width == 640
    assert settings.media.workspace.receive_enabled is True
    assert settings.media.workspace.receive_url == "https://media/workspace-cam/whep"
    assert settings.media.reconnect_interval_seconds == 2.5


def test_default_camera_roles_match_connected_device_capabilities() -> None:
    settings = Settings(_env_file=None)

    assert "Alcorlink" in settings.media.user.device
    assert (settings.media.user.width, settings.media.user.height) == (1920, 1080)
    assert "ABKO_APC930_QHD_WEBCAM" in settings.media.workspace.device
    assert (settings.media.workspace.width, settings.media.workspace.height) == (
        2592,
        1944,
    )
    assert settings.media.workspace.publish_url.endswith("/workspace-cam/whip")
    assert settings.media.posture.device == "/dev/posture-cam"


@pytest.mark.parametrize(
    "media",
    [
        {"user": {"device": " "}},
        {"workspace": {"publish_url": "http://media/workspace/whep"}},
        {"posture": {"receive_url": "http://media/posture/whip"}},
        {"user": {"width": 0}},
        {"posture": {"fps": True}},
        {"reconnect_interval_seconds": 0},
        {"reconnect_interval_seconds": 31},
    ],
)
def test_invalid_camera_media_settings_are_rejected(media: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Settings(media=media, _env_file=None)


def test_voice_is_disabled_by_default_without_api_key() -> None:
    settings = Settings(_env_file=None)

    assert settings.voice.enabled is False
    assert settings.openai.api_key is None
    assert settings.openai.response_model == "gpt-5.6-terra"
    assert settings.voice.wakeword_model_path == Path(
        "assets/voice/models/hi_smarty_ko_mixed_v0_2_0.onnx"
    )
    assert settings.voice.wakeword_threshold == 0.25
    assert settings.voice.wakeword_consecutive_frames == 1
    assert settings.voice.wakeword_inference_interval_frames == 5
    assert settings.voice.recording_timeout_seconds == 20.0
    assert settings.voice.turn_timeout_seconds == 120.0
    assert settings.voice.followup_timeout_seconds == 4.0
    assert settings.voice.post_playback_guard_seconds == 1.0
    assert settings.voice.session_history_item_cap == 24


@pytest.mark.parametrize("item_cap", [0, -1, 201])
def test_voice_session_history_item_cap_must_be_positive_and_bounded(item_cap: int) -> None:
    with pytest.raises(ValidationError):
        Settings(voice={"session_history_item_cap": item_cap}, _env_file=None)


@pytest.mark.parametrize(
    "voice",
    [
        {"speech_start_timeout_seconds": 3, "recording_timeout_seconds": 3},
        {"recording_timeout_seconds": 20, "turn_timeout_seconds": 20},
    ],
)
def test_voice_turn_timeouts_must_be_strictly_ordered(voice: dict[str, float]) -> None:
    with pytest.raises(ValidationError):
        Settings(voice=voice, _env_file=None)


def test_enabled_voice_requires_api_key() -> None:
    with pytest.raises(ValidationError, match="OpenAI API key"):
        Settings(voice={"enabled": True}, _env_file=None)


def test_profile_memory_is_disabled_by_default_and_requires_openai_key() -> None:
    settings = Settings(_env_file=None)

    assert settings.profile_memory.enabled is False
    assert settings.profile_memory.data_path == Path("data/mem0")
    assert settings.profile_memory.history_db_path == Path("data/mem0/history.db")
    assert settings.profile_memory.collection_name == "smart_desk_profile_memory_v1"
    assert settings.profile_memory.embedding_model == "text-embedding-3-small"
    assert settings.profile_memory.embedding_dimensions == 1536
    assert settings.profile_memory.search_limit == 5
    assert settings.profile_memory.write_timeout_seconds == 8
    assert settings.profile_memory.fact_limit == 500
    assert settings.profile_memory.circuit_failure_threshold == 3
    assert settings.profile_memory.circuit_open_seconds == 30

    with pytest.raises(ValidationError, match="OpenAI API key"):
        Settings(profile_memory={"enabled": True}, _env_file=None)


def test_voice_debug_requires_voice_and_distinct_port() -> None:
    with pytest.raises(ValidationError, match="Voice debug"):
        Settings(voice_debug={"enabled": True}, _env_file=None)

    with pytest.raises(ValidationError, match="포트"):
        Settings(
            voice={"enabled": True},
            voice_debug={"enabled": True, "port": 9090},
            openai={"api_key": "test-key"},
            _env_file=None,
        )

    settings = Settings(
        voice={"enabled": True},
        voice_debug={"enabled": True, "host": " 0.0.0.0 ", "port": 10_000},
        openai={"api_key": "test-key"},
        _env_file=None,
    )
    assert settings.voice_debug.host == "0.0.0.0"
    assert settings.voice_debug.port == 10_000


def test_voice_environment_and_blank_values_are_normalized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SMART_DESK_VOICE__ENABLED", "true")
    monkeypatch.setenv("SMART_DESK_OPENAI__API_KEY", "test-key")
    monkeypatch.setenv("SMART_DESK_VOICE__INPUT_DEVICE_NAME", "  Desk Mic  ")
    monkeypatch.setenv("SMART_DESK_VOICE__OUTPUT_DEVICE_NAME", "   ")

    settings = Settings(_env_file=None)

    assert settings.voice.enabled is True
    assert settings.openai.api_key is not None
    assert settings.openai.api_key.get_secret_value() == "test-key"
    assert settings.voice.input_device_name == "Desk Mic"
    assert settings.voice.output_device_name is None


@pytest.mark.parametrize(
    "voice",
    [
        {"followup_preroll_seconds": 1.0, "input_queue_frames": 8},
        {"post_playback_guard_seconds": 2.0, "followup_timeout_seconds": 2.0},
    ],
)
def test_invalid_voice_cross_field_settings_are_rejected(
    voice: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        Settings(voice=voice, _env_file=None)


@pytest.mark.parametrize(
    "openai",
    [
        {"response_model": " "},
    ],
)
def test_invalid_openai_settings_are_rejected(openai: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Settings(openai=openai, _env_file=None)
