"""배포 후 브라우저가 예전 번들을 붙잡지 않도록 캐시 헤더를 검증한다."""

from httpx import ASGITransport, AsyncClient
import pytest

from smart_desk.application import create_application
from smart_desk.config.settings import DashboardSettings, Settings, StorageSettings
from smart_desk.core.container import AppContainer
from smart_desk.core.runtime import RuntimeState
from smart_desk.core.task_manager import TaskManager
from smart_desk.modules.dashboard import DashboardService
from smart_desk.modules.profiles import ActivityModeRepository, ProfileRepository
from smart_desk.storage import SQLiteDatabase


@pytest.fixture
async def client(tmp_path):
    build = tmp_path / "dist"
    (build / "assets").mkdir(parents=True)
    (build / "index.html").write_text(
        '<!doctype html><script src="/assets/index-abc123.js"></script>', encoding="utf-8"
    )
    (build / "assets" / "index-abc123.js").write_text("console.log(1)", encoding="utf-8")

    settings = Settings(
        environment="test",
        storage=StorageSettings(database_path=tmp_path / "desk.db"),
        dashboard=DashboardSettings(serve_frontend=True, frontend_directory=build),
        _env_file=None,
    )
    database = SQLiteDatabase(settings.storage.database_path)
    profiles = ProfileRepository(database)
    desk = object()
    container = AppContainer(
        settings=settings, runtime=RuntimeState(), task_manager=TaskManager(),
        database=database, profiles=profiles,
        activity_modes=ActivityModeRepository(database),
        dashboard=DashboardService(desk, profiles),
        mqtt=object(), height_monitor=object(), relay=object(), desk=desk,
    )  # type: ignore[arg-type]
    application = create_application(settings=settings, container=container)
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as connected:
        yield connected


async def test_index_is_revalidated_so_new_builds_are_picked_up(client) -> None:
    response = await client.get("/")

    assert response.status_code == 200
    assert "no-cache" in response.headers["cache-control"]


async def test_hashed_assets_are_cached_long(client) -> None:
    response = await client.get("/assets/index-abc123.js")

    assert response.status_code == 200
    assert "immutable" in response.headers["cache-control"]


async def test_api_responses_keep_their_own_cache_policy(client) -> None:
    response = await client.get("/health/live")

    assert response.status_code == 200
    # 정적 파일 정책이 API 응답까지 건드리지 않아야 한다.
    assert "immutable" not in response.headers.get("cache-control", "")
