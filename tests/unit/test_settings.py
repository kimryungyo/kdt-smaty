"""환경변수 설정 검증 테스트."""

import pytest
from pydantic import ValidationError

from smart_desk.config.settings import Settings


def test_nested_environment_variable_is_loaded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMART_DESK_SERVER__PORT", "9191")

    settings = Settings(_env_file=None)

    assert settings.server.port == 9191


def test_multiple_workers_are_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMART_DESK_SERVER__WORKERS", "2")

    with pytest.raises(ValidationError, match="worker를 하나만"):
        Settings(_env_file=None)


def test_operation_range_cannot_exceed_physical_maximum(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SMART_DESK_DESK__OPERATION_MAX_CM", "119")

    with pytest.raises(ValidationError, match="물리 최대 높이 118cm"):
        Settings(_env_file=None)


def test_default_desk_maximum_is_118_cm() -> None:
    settings = Settings(_env_file=None)

    assert settings.desk.physical_max_cm == 118.0
    assert settings.desk.operation_max_cm == 118.0


def test_frontend_directory_can_be_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMART_DESK_DASHBOARD__FRONTEND_DIRECTORY", "custom-ui/dist")

    settings = Settings(_env_file=None)

    assert settings.dashboard.frontend_directory.as_posix() == "custom-ui/dist"
