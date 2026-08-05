"""FastAPI lifespan과 health API 통합 테스트."""

from httpx import ASGITransport, AsyncClient
import pytest

from smart_desk.application import create_application
from smart_desk.config.settings import DashboardSettings, Settings
from smart_desk.core.container import get_container
from smart_desk.core.runtime import ApplicationStatus


async def test_health_endpoints_report_ready_during_lifespan() -> None:
    settings = Settings(
        environment="test",
        dashboard=DashboardSettings(serve_frontend=False),
        _env_file=None,
    )
    application = create_application(settings=settings)

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
