"""환경변수 설정 검증 테스트."""

import pytest
from pydantic import ValidationError

from smart_desk.config.settings import Settings


def test_nested_environment_variable_is_loaded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMART_DESK_SERVER__PORT", "9191")

    settings = Settings(_env_file=None)

    assert settings.server.port == 9191


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
    assert settings.desk.fine_hold_ms == 100
    assert settings.desk.pulse_refresh_interval_seconds == 0.1


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
