"""Dashboard와 profile HTTP 계약을 실제 SQLite와 fake Desk로 검증한다."""

from datetime import UTC, datetime

from httpx import ASGITransport, AsyncClient

from smart_desk.application import create_application
from smart_desk.config.settings import DashboardSettings, Settings, StorageSettings
from smart_desk.core.container import AppContainer
from smart_desk.core.runtime import ApplicationStatus, RuntimeState
from smart_desk.core.task_manager import TaskManager
from smart_desk.modules.dashboard import DashboardService
from smart_desk.modules.desk.controller import DeskCommandRejectedError
from smart_desk.modules.desk.models import (
    DeskSnapshot,
    DeskState,
    Direction,
    HeightSnapshot,
    HeightStatus,
    RelayEvent,
    RelaySnapshot,
    RelayState,
)
from smart_desk.modules.profiles import ProfileRepository
from smart_desk.storage import SQLiteDatabase


class FakeDesk:
    def __init__(self) -> None:
        self.reject = False
        self.unavailable = False
        self.snapshot = DeskSnapshot(
            state=DeskState.IDLE,
            height=HeightSnapshot(90.0, datetime(2026, 8, 8, tzinfo=UTC), HeightStatus.ONLINE),
            relay=RelaySnapshot(RelayEvent.ONLINE, RelayState.STOP, "test", None, None, datetime(2026, 8, 8, tzinfo=UTC), None),
            target_height_cm=None,
            direction=None,
            detail="ready",
            last_error=None,
            updated_at=datetime(2026, 8, 8, tzinfo=UTC),
        )

    def get_snapshot(self) -> DeskSnapshot:
        return self.snapshot

    async def hold_up(self) -> None:
        if self.reject:
            raise DeskCommandRejectedError("릴레이가 준비되지 않았습니다.")
        if self.unavailable:
            raise RuntimeError("controller not running")

    async def hold_down(self) -> None: pass
    async def stop_motion(self, _reason: str) -> None: pass
    async def set_target(self, _target: float) -> None: pass


async def test_dashboard_api_contract_and_profile_crud(tmp_path) -> None:
    settings = Settings(environment="test", storage=StorageSettings(database_path=tmp_path / "desk.db"), dashboard=DashboardSettings(serve_frontend=False), _env_file=None)
    database = SQLiteDatabase(settings.storage.database_path)
    profiles = ProfileRepository(database)
    desk = FakeDesk()
    runtime = RuntimeState()
    container = AppContainer(settings=settings, runtime=runtime, task_manager=TaskManager(), database=database, profiles=profiles, dashboard=DashboardService(desk, profiles), mqtt=object(), height_monitor=object(), relay=object(), desk=desk)  # type: ignore[arg-type]
    application = create_application(settings=settings, container=container)
    await database.start()
    runtime.mark(ApplicationStatus.READY, "test ready")

    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/status")
        assert response.status_code == 200
        assert response.json()["height"] == {"heightCm": 90.0, "observedAt": "2026-08-08T00:00:00Z", "status": "ONLINE"}
        assert response.json()["targetHeightCm"] is None

        created = await client.post("/api/profiles", json={"name": " 홍길동 ", "sittingHeightCm": 80.0, "standingHeightCm": 105.0, "ledColor": "ff3000"})
        assert created.status_code == 201
        assert created.json()["name"] == "홍길동"
        assert created.json()["ledColor"] == "FF3000"
        profile_id = created.json()["id"]

        updated = await client.patch(f"/api/profiles/{profile_id}", json={"ledColor": None})
        assert updated.status_code == 200
        assert updated.json()["ledColor"] is None
        assert (await client.get("/api/profiles")).json()[0]["id"] == profile_id
        assert (await client.delete(f"/api/profiles/{profile_id}")).status_code == 204
        assert (await client.get(f"/api/profiles/{profile_id}")).status_code == 404

        assert (await client.post("/api/target", json={"action": "SET", "targetCm": 116})).status_code == 422
        assert (await client.post("/api/control", json={"action": "HOLD", "direction": "UP", "unknown": True})).status_code == 422
        desk.reject = True
        assert (await client.post("/api/control", json={"action": "HOLD", "direction": "UP"})).status_code == 409
        desk.reject = False
        desk.unavailable = True
        assert (await client.post("/api/control", json={"action": "HOLD", "direction": "UP"})).status_code == 503

    await database.stop()
