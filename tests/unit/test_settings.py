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


def test_frontend_directory_can_be_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMART_DESK_DASHBOARD__FRONTEND_DIRECTORY", "custom-ui/dist")

    settings = Settings(_env_file=None)

    assert settings.dashboard.frontend_directory.as_posix() == "custom-ui/dist"
