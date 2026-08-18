"""작업 모드 사용 시간 집계 HTTP 계약을 검증한다."""

from httpx import ASGITransport, AsyncClient
import pytest

from smart_desk.application import create_application
from smart_desk.config.settings import DashboardSettings, Settings, StorageSettings
from smart_desk.core.container import AppContainer
from smart_desk.core.runtime import RuntimeState
from smart_desk.core.task_manager import TaskManager
from smart_desk.modules.dashboard import DashboardService
from smart_desk.modules.profiles import (
    ActivityModeRepository, ProfileCreate, ProfileRepository,
)
from smart_desk.modules.profiles.usage import ActivityModeUsageRepository
from smart_desk.storage import SQLiteDatabase


@pytest.fixture
async def api(tmp_path):
    settings = Settings(
        environment="test",
        storage=StorageSettings(database_path=tmp_path / "desk.db"),
        dashboard=DashboardSettings(serve_frontend=False),
        _env_file=None,
    )
    database = SQLiteDatabase(settings.storage.database_path)
    profiles = ProfileRepository(database)
    usage = ActivityModeUsageRepository(database)
    desk = object()
    container = AppContainer(
        settings=settings, runtime=RuntimeState(), task_manager=TaskManager(),
        database=database, profiles=profiles,
        activity_modes=ActivityModeRepository(database),
        dashboard=DashboardService(desk, profiles),
        mqtt=object(), height_monitor=object(), relay=object(), desk=desk,
        mode_usage=usage,
    )  # type: ignore[arg-type]
    application = create_application(settings=settings, container=container)
    await database.start()
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, profiles, usage
    await database.stop()


async def test_usage_is_empty_before_any_mode_is_used(api) -> None:
    client, _profiles, _usage = api

    response = await client.get("/api/activity-modes/usage")

    assert response.status_code == 200
    body = response.json()
    assert body["totalSeconds"] == 0
    assert body["modes"] == []
    assert len(body["days"]) == 7


async def test_usage_reports_recorded_intervals(api) -> None:
    client, profiles, usage = api
    profile = await profiles.create_profile(
        ProfileCreate(name="사용자", sittingHeightCm=80, standingHeightCm=105)
    )
    await usage.start_interval(profile.id, "mode-study", "공부")
    await usage.close_open_intervals(profile.id)

    body = (await client.get("/api/activity-modes/usage?days=3")).json()

    assert len(body["days"]) == 3
    assert [mode["name"] for mode in body["modes"]] in ([], ["공부"])


@pytest.mark.parametrize("days", [0, 32, -1])
async def test_out_of_range_day_counts_are_rejected(api, days: int) -> None:
    client, _profiles, _usage = api

    assert (await client.get(f"/api/activity-modes/usage?days={days}")).status_code == 422
