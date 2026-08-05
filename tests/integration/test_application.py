"""FastAPI lifespan과 health API 통합 테스트."""

from httpx import ASGITransport, AsyncClient
import pytest

from smart_desk.application import create_application
from smart_desk.config.settings import DashboardSettings, Settings
from smart_desk.core.container import AppContainer, ResourceRegistration, get_container
from smart_desk.core.runtime import ApplicationStatus, RuntimeState
from smart_desk.core.task_manager import TaskManager
from smart_desk.modules.mqtt.client import MqttStartupError


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


def build_test_container(settings: Settings) -> tuple[AppContainer, FakeMqttClient]:
    mqtt = FakeMqttClient()
    container = AppContainer(
        settings=settings,
        runtime=RuntimeState(),
        task_manager=TaskManager(),
        mqtt=mqtt,  # type: ignore[arg-type]
    )
    container.register(
        ResourceRegistration(
            name="mqtt",
            resource=mqtt,
            startup_order=10,
            shutdown_order=10,
        )
    )
    return container, mqtt


def build_failing_test_container(settings: Settings) -> AppContainer:
    mqtt = FailingMqttClient()
    container = AppContainer(
        settings=settings,
        runtime=RuntimeState(),
        task_manager=TaskManager(),
        mqtt=mqtt,  # type: ignore[arg-type]
    )
    container.register(
        ResourceRegistration(
            name="mqtt",
            resource=mqtt,
            startup_order=10,
            shutdown_order=10,
        )
    )
    return container


async def test_health_endpoints_report_ready_during_lifespan() -> None:
    settings = Settings(
        environment="test",
        dashboard=DashboardSettings(serve_frontend=False),
        _env_file=None,
    )
    container, mqtt = build_test_container(settings)
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


async def test_mqtt_startup_failure_prevents_application_start() -> None:
    settings = Settings(
        environment="test",
        dashboard=DashboardSettings(serve_frontend=False),
        _env_file=None,
    )
    container = build_failing_test_container(settings)
    application = create_application(settings=settings, container=container)

    with pytest.raises(MqttStartupError, match="test startup failure"):
        async with application.router.lifespan_context(application):
            pass

    assert container.runtime.snapshot().status is ApplicationStatus.FAILED


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

    assert root_response.status_code == 200
    assert "SMART DESK TEST" in root_response.text
    assert fallback_response.status_code == 200
    assert "SMART DESK TEST" in fallback_response.text


def test_production_requires_frontend_build(tmp_path) -> None:
    settings = Settings(
        environment="production",
        dashboard=DashboardSettings(frontend_directory=tmp_path / "missing"),
        _env_file=None,
    )

    with pytest.raises(RuntimeError, match="React 빌드 결과"):
        create_application(settings=settings)
