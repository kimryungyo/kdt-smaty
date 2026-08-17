"""FastAPI lifespan과 health API 통합 테스트."""

import asyncio
from pathlib import Path

from httpx import ASGITransport, AsyncClient
import pytest
import serial

from smart_desk.application import create_application
from smart_desk.bootstrap import build_container
from smart_desk.config.settings import DashboardSettings, Settings, StorageSettings
from smart_desk.core.container import AppContainer, ResourceRegistration, get_container
from smart_desk.core.runtime import ApplicationStatus, RuntimeState
from smart_desk.core.task_manager import TaskManager
from smart_desk.modules.dashboard import DashboardService
from smart_desk.modules.desk.models import HeightStatus
from smart_desk.modules.mqtt.client import MqttStartupError
from smart_desk.modules.profiles import ActivityModeRepository, ProfileRepository
from smart_desk.storage import SQLiteDatabase, StorageCorruptedError, StorageNotReadyError


class FakeMqttClient:
    """HTTP 애플리케이션 테스트가 실제 broker에 연결되지 않게 하는 resource."""

    def __init__(self) -> None:
        self.start_count = 0
        self.stop_count = 0

    async def start(self) -> None:
        self.start_count += 1

    async def stop(self) -> None:
        self.stop_count += 1

    def is_connected(self) -> bool:
        return self.start_count > self.stop_count


class FailingMqttClient(FakeMqttClient):
    """lifespan에서 MQTT 시작 실패를 재현한다."""

    async def start(self) -> None:
        self.start_count += 1
        raise MqttStartupError("test startup failure")


class FakeHeightMonitor:
    """애플리케이션 테스트가 실제 Arduino에 접근하지 않게 하는 resource."""

    def __init__(self) -> None:
        self.start_count = 0
        self.stop_count = 0

    async def start(self) -> None:
        self.start_count += 1

    async def stop(self) -> None:
        self.stop_count += 1


class FakeDeskController(FakeHeightMonitor):
    """애플리케이션 테스트용 lifecycle 책상 제어기."""


class VoiceErrorResource(FakeHeightMonitor):
    """start가 예외를 전파하지 않고 Voice-only ERROR가 된 상황을 재현한다."""

    async def start(self) -> None:
        self.start_count += 1


def build_test_container(
    settings: Settings,
) -> tuple[AppContainer, FakeMqttClient, FakeHeightMonitor]:
    database = SQLiteDatabase(settings.storage.database_path)
    profiles = ProfileRepository(database)
    activity_modes = ActivityModeRepository(database)
    mqtt = FakeMqttClient()
    height_monitor = FakeHeightMonitor()
    desk = FakeDeskController()
    dashboard = DashboardService(desk, profiles)  # type: ignore[arg-type]
    container = AppContainer(
        settings=settings,
        runtime=RuntimeState(),
        task_manager=TaskManager(),
        database=database,
        profiles=profiles,
        activity_modes=activity_modes,
        dashboard=dashboard,
        mqtt=mqtt,  # type: ignore[arg-type]
        height_monitor=height_monitor,  # type: ignore[arg-type]
        relay=object(),  # type: ignore[arg-type]
        desk=desk,  # type: ignore[arg-type]
    )
    container.register(
        ResourceRegistration(
            name="sqlite",
            resource=database,
            startup_order=5,
            shutdown_order=5,
        )
    )
    container.register(
        ResourceRegistration(
            name="mqtt",
            resource=mqtt,
            startup_order=10,
            shutdown_order=10,
        )
    )
    container.register(
        ResourceRegistration(
            name="desk-controller",
            resource=desk,
            startup_order=30,
            shutdown_order=30,
        )
    )
    container.register(
        ResourceRegistration(
            name="desk-height-monitor",
            resource=height_monitor,
            startup_order=20,
            shutdown_order=20,
        )
    )
    return container, mqtt, height_monitor


def build_failing_test_container(settings: Settings) -> AppContainer:
    database = SQLiteDatabase(settings.storage.database_path)
    profiles = ProfileRepository(database)
    activity_modes = ActivityModeRepository(database)
    mqtt = FailingMqttClient()
    height_monitor = FakeHeightMonitor()
    desk = FakeDeskController()
    dashboard = DashboardService(desk, profiles)  # type: ignore[arg-type]
    container = AppContainer(
        settings=settings,
        runtime=RuntimeState(),
        task_manager=TaskManager(),
        database=database,
        profiles=profiles,
        activity_modes=activity_modes,
        dashboard=dashboard,
        mqtt=mqtt,  # type: ignore[arg-type]
        height_monitor=height_monitor,  # type: ignore[arg-type]
        relay=object(),  # type: ignore[arg-type]
        desk=desk,  # type: ignore[arg-type]
    )
    container.register(
        ResourceRegistration(
            name="sqlite",
            resource=database,
            startup_order=5,
            shutdown_order=5,
        )
    )
    container.register(
        ResourceRegistration(
            name="mqtt",
            resource=mqtt,
            startup_order=10,
            shutdown_order=10,
        )
    )
    container.register(
        ResourceRegistration(
            name="desk-height-monitor",
            resource=height_monitor,
            startup_order=20,
            shutdown_order=20,
        )
    )
    return container


async def test_health_endpoints_report_ready_during_lifespan(tmp_path: Path) -> None:
    settings = Settings(
        environment="test",
        storage=StorageSettings(database_path=tmp_path / "smart-desk.db"),
        dashboard=DashboardSettings(serve_frontend=False),
        _env_file=None,
    )
    container, mqtt, height_monitor = build_test_container(settings)
    application = create_application(settings=settings, container=container)

    async with application.router.lifespan_context(application):
        transport = ASGITransport(app=application)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            live_response = await client.get("/health/live")
            ready_response = await client.get("/health/ready")

            assert live_response.status_code == 200
            assert live_response.json()["status"] == "alive"
            assert ready_response.status_code == 200
            assert ready_response.json()["status"] == "ready"

    assert get_container().runtime.snapshot().status is ApplicationStatus.STOPPED
    assert mqtt.start_count == 1
    assert mqtt.stop_count == 1
    assert height_monitor.start_count == 1
    assert height_monitor.stop_count == 1


async def test_mqtt_startup_failure_prevents_application_start(tmp_path: Path) -> None:
    settings = Settings(
        environment="test",
        storage=StorageSettings(database_path=tmp_path / "smart-desk.db"),
        dashboard=DashboardSettings(serve_frontend=False),
        _env_file=None,
    )
    container = build_failing_test_container(settings)
    application = create_application(settings=settings, container=container)

    with pytest.raises(MqttStartupError, match="test startup failure"):
        async with application.router.lifespan_context(application):
            pass

    assert container.runtime.snapshot().status is ApplicationStatus.FAILED
    with pytest.raises(StorageNotReadyError):
        await container.database.read(lambda connection: None)


async def test_sqlite_startup_failure_prevents_later_resources(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "smart-desk.db"
    database_path.write_bytes(b"not sqlite")
    settings = Settings(
        environment="test",
        storage=StorageSettings(database_path=database_path),
        dashboard=DashboardSettings(serve_frontend=False),
        _env_file=None,
    )
    container, mqtt, height_monitor = build_test_container(settings)
    application = create_application(settings=settings, container=container)

    with pytest.raises(StorageCorruptedError):
        async with application.router.lifespan_context(application):
            pass

    assert container.runtime.snapshot().status is ApplicationStatus.FAILED
    assert mqtt.start_count == 0
    assert height_monitor.start_count == 0
    assert database_path.read_bytes() == b"not sqlite"


async def test_missing_arduino_does_not_prevent_application_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        environment="test",
        storage=StorageSettings(database_path=tmp_path / "smart-desk.db"),
        dashboard=DashboardSettings(serve_frontend=False),
        serial={"reconnect_interval_seconds": 0.01},
        _env_file=None,
    )
    container = build_container(settings)

    async def start_mqtt_without_broker() -> None:
        return None

    async def stop_mqtt_without_broker() -> None:
        return None

    def missing_serial_device(**_kwargs: object):
        raise serial.SerialException("Arduino missing")

    monkeypatch.setattr(container.mqtt, "start", start_mqtt_without_broker)
    monkeypatch.setattr(container.mqtt, "stop", stop_mqtt_without_broker)
    monkeypatch.setattr(
        "smart_desk.modules.serial.source.serial.Serial",
        missing_serial_device,
    )
    application = create_application(settings=settings, container=container)

    async with application.router.lifespan_context(application):
        async with asyncio.timeout(0.5):
            while container.height_monitor.get_snapshot().status is not HeightStatus.ERROR:
                await asyncio.sleep(0)

        assert container.runtime.snapshot().status is ApplicationStatus.READY
        assert container.height_monitor.get_snapshot().status is HeightStatus.ERROR

    assert container.runtime.snapshot().status is ApplicationStatus.STOPPED


async def test_voice_only_start_error_does_not_fail_application(
    tmp_path: Path,
) -> None:
    settings = Settings(
        environment="test",
        storage=StorageSettings(database_path=tmp_path / "smart-desk.db"),
        dashboard=DashboardSettings(serve_frontend=False),
        _env_file=None,
    )
    container, mqtt, height_monitor = build_test_container(settings)
    voice = VoiceErrorResource()
    container.register(
        ResourceRegistration(
            name="voice",
            resource=voice,
            startup_order=70,
            shutdown_order=70,
        )
    )
    application = create_application(settings=settings, container=container)

    async with application.router.lifespan_context(application):
        assert container.runtime.snapshot().status is ApplicationStatus.READY
        assert mqtt.start_count == 1
        assert height_monitor.start_count == 1
        assert voice.start_count == 1

    assert voice.stop_count == 1


async def test_react_build_and_spa_fallback_are_served(tmp_path) -> None:
    (tmp_path / "index.html").write_text(
        "<!doctype html><title>SMART DESK TEST</title>",
        encoding="utf-8",
    )
    settings = Settings(
        environment="test",
        dashboard=DashboardSettings(frontend_directory=tmp_path),
        _env_file=None,
    )
    application = create_application(settings=settings)

    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        root_response = await client.get("/", headers={"Accept": "text/html"})
        fallback_response = await client.get(
            "/dashboard/settings",
            headers={"Accept": "text/html"},
        )
        api_response = await client.get("/api/status")

    assert root_response.status_code == 200
    assert "SMART DESK TEST" in root_response.text
    assert fallback_response.status_code == 200
    assert "SMART DESK TEST" in fallback_response.text
    assert api_response.status_code == 200
    assert api_response.headers["content-type"].startswith("application/json")


def test_production_requires_frontend_build(tmp_path) -> None:
    settings = Settings(
        environment="production",
        dashboard=DashboardSettings(frontend_directory=tmp_path / "missing"),
        _env_file=None,
    )

    with pytest.raises(RuntimeError, match="React 빌드 결과"):
        create_application(settings=settings)
