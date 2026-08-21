"""Dashboard와 profile HTTP 계약을 실제 SQLite와 fake Desk로 검증한다."""

from datetime import UTC, datetime

from httpx import ASGITransport, AsyncClient
import pytest

from smart_desk.application import create_application
from smart_desk.modules.automation.models import (
    AutomationSnapshot,
    AutomationState,
    ControlMode,
)
from smart_desk.modules.automation.service import (
    AutomationConflictError,
    AutomationNotFoundError,
)
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
from smart_desk.modules.profiles import ActivityModeRepository, ProfileRepository
from smart_desk.storage import SQLiteDatabase
from smart_desk.modules.voice.models import VoiceSnapshot, VoiceState


class FakeDesk:
    def __init__(self) -> None:
        self.reject = False
        self.unavailable = False
        self.commands: list[str] = []
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
        self.commands.append("hold_up")
        if self.reject:
            raise DeskCommandRejectedError("릴레이가 준비되지 않았습니다.")
        if self.unavailable:
            raise RuntimeError("controller not running")

    async def hold_down(self) -> None:
        self.commands.append("hold_down")

    async def stop_motion(self, _reason: str) -> None:
        self.commands.append("stop_motion")

    async def set_target(self, _target: float) -> None:
        self.commands.append("set_target")


class ApiAutomation:
    """Small public-port fake for HTTP serialization and error mapping."""

    def __init__(self) -> None:
        self.snapshot = AutomationSnapshot(
            None, None, None, AutomationState.WAITING_USER, None, None, None,
            None, None, (), None, None, 0, 0, "STARTUP", "SYSTEM",
            datetime(2026, 8, 8, tzinfo=UTC), datetime(2026, 8, 8, tzinfo=UTC),
        )
        self.active_mode = True

    def get_snapshot(self) -> AutomationSnapshot:
        return self.snapshot

    async def set_control_mode(self, _mode: ControlMode, expected_session_id: str) -> None:
        if expected_session_id != "current-session":
            raise AutomationConflictError("SESSION_MISMATCH")
        if _mode is ControlMode.AUTO:
            raise RuntimeError("desk unavailable")

    async def set_activity_mode(self, key: str, expected_session_id: str) -> None:
        if expected_session_id != "current-session" or key == "anonymous":
            raise AutomationConflictError("SESSION_MISMATCH")
        if key == "missing":
            raise AutomationNotFoundError("mode missing")
        if key == "storage":
            raise RuntimeError("storage unavailable")

    async def delete_activity_mode(self, _mode_id: str) -> None:
        if self.active_mode:
            raise AutomationConflictError("ACTIVE_ACTIVITY_MODE")


class FakeVoice:
    def __init__(self, snapshot: VoiceSnapshot) -> None:
        self.snapshot = snapshot

    def get_snapshot(self) -> VoiceSnapshot:
        return self.snapshot


async def test_voice_status_api_distinguishes_disabled_and_active_snapshot(tmp_path) -> None:
    settings = Settings(
        environment="test", storage=StorageSettings(database_path=tmp_path / "desk.db"),
        dashboard=DashboardSettings(serve_frontend=False), _env_file=None,
    )
    database = SQLiteDatabase(settings.storage.database_path)
    profiles = ProfileRepository(database)
    activity_modes = ActivityModeRepository(database)
    desk = FakeDesk()
    container = AppContainer(
        settings=settings, runtime=RuntimeState(), task_manager=TaskManager(), database=database,
        profiles=profiles, activity_modes=activity_modes, dashboard=DashboardService(desk, profiles),
        mqtt=object(), height_monitor=object(), relay=object(), desk=desk,
    )  # type: ignore[arg-type]
    application = create_application(settings=settings, container=container)
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        disabled = await client.get("/api/voice/status")
        assert disabled.status_code == 200
        assert disabled.json() == {
            "state": "DISABLED", "lastTransitionAt": None,
            "followupExpiresAt": None, "lastError": None,
        }

        container.voice = FakeVoice(VoiceSnapshot(
            VoiceState.WAITING_FOLLOWUP, datetime(2026, 8, 8, tzinfo=UTC),
            datetime(2026, 8, 8, 0, 0, 4, tzinfo=UTC), None,
        ))  # type: ignore[assignment]
        active = await client.get("/api/voice/status")

    assert active.status_code == 200
    assert active.json() == {
        "state": "WAITING_FOLLOWUP", "lastTransitionAt": "2026-08-08T00:00:00Z",
        "followupExpiresAt": "2026-08-08T00:00:04Z", "lastError": None,
    }


async def test_tilt_contract_is_explicitly_unavailable_until_hardware_exists(tmp_path) -> None:
    settings = Settings(
        environment="test", storage=StorageSettings(database_path=tmp_path / "desk.db"),
        dashboard=DashboardSettings(serve_frontend=False), _env_file=None,
    )
    database = SQLiteDatabase(settings.storage.database_path)
    profiles = ProfileRepository(database)
    activity_modes = ActivityModeRepository(database)
    desk = FakeDesk()
    container = AppContainer(
        settings=settings, runtime=RuntimeState(), task_manager=TaskManager(), database=database,
        profiles=profiles, activity_modes=activity_modes, dashboard=DashboardService(desk, profiles),
        mqtt=object(), height_monitor=object(), relay=object(), desk=desk,
    )  # type: ignore[arg-type]
    application = create_application(settings=settings, container=container)
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        current = await client.get("/api/tilt/status")
        command = await client.put("/api/tilt/target", json={"level": 3})
        invalid = await client.put("/api/tilt/target", json={"level": 6})

    assert current.status_code == 200
    assert current.json() | {"updatedAt": None} == {
        "status": "UNAVAILABLE", "level": None, "targetLevel": None,
        "positionMm": None, "positionValid": False,
        "minLevel": 0, "maxLevel": 4,
        "detail": "틸팅 하드웨어가 아직 활성화되지 않았습니다.",
        "lastError": None, "updatedAt": None,
    }
    assert command.status_code == 503
    assert invalid.status_code == 422


async def test_automation_api_uses_camel_case_and_preserves_error_meanings(tmp_path) -> None:
    settings = Settings(
        environment="test",
        storage=StorageSettings(database_path=tmp_path / "desk.db"),
        dashboard=DashboardSettings(serve_frontend=False),
        _env_file=None,
    )
    database = SQLiteDatabase(settings.storage.database_path)
    profiles = ProfileRepository(database)
    activity_modes = ActivityModeRepository(database)
    desk = FakeDesk()
    automation = ApiAutomation()
    container = AppContainer(
        settings=settings,
        runtime=RuntimeState(),
        task_manager=TaskManager(),
        database=database,
        profiles=profiles,
        activity_modes=activity_modes,
        dashboard=DashboardService(desk, profiles, automation),
        mqtt=object(),
        height_monitor=object(),
        relay=object(),
        desk=desk,
        automation=automation,  # type: ignore[arg-type]
    )
    application = create_application(settings=settings, container=container)

    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        status_response = await client.get("/api/automation/status")
        assert status_response.status_code == 200
        assert status_response.json()["sessionId"] is None
        assert "session_id" not in status_response.json()

        assert (await client.put("/api/desk/control-mode", json={
            "controlMode": "MANUAL", "expectedSessionId": "current-session",
        })).status_code == 200
        assert (await client.put("/api/desk/control-mode", json={
            "controlMode": "MANUAL", "expectedSessionId": "stale",
        })).status_code == 409
        assert (await client.put("/api/desk/control-mode", json={
            "controlMode": "AUTO", "expectedSessionId": "current-session",
        })).status_code == 503
        assert (await client.put("/api/desk/control-mode", json={
            "controlMode": "MANUAL", "expectedSessionId": "current-session", "extra": True,
        })).status_code == 422

        assert (await client.put("/api/desk/activity-mode", json={
            "activityModeKey": "anonymous", "expectedSessionId": "current-session",
        })).status_code == 409
        assert (await client.put("/api/desk/activity-mode", json={
            "activityModeKey": "missing", "expectedSessionId": "current-session",
        })).status_code == 404
        assert (await client.put("/api/desk/activity-mode", json={
            "activityModeKey": "storage", "expectedSessionId": "current-session",
        })).status_code == 503
        assert (await client.put("/api/desk/activity-mode", json={
            "activityModeKey": "default", "expectedSessionId": "stale",
        })).status_code == 409

        assert (await client.delete("/api/activity-modes/active-custom")).status_code == 409
        automation.active_mode = False
        assert (await client.delete("/api/activity-modes/active-custom")).status_code == 204


@pytest.mark.parametrize(
    "runtime_status", [ApplicationStatus.FAILED, ApplicationStatus.STARTING]
)
async def test_dashboard_api_contract_and_storage_crud_ignore_global_readiness(
    tmp_path, runtime_status: ApplicationStatus
) -> None:
    settings = Settings(environment="test", storage=StorageSettings(database_path=tmp_path / "desk.db"), dashboard=DashboardSettings(serve_frontend=False), _env_file=None)
    database = SQLiteDatabase(settings.storage.database_path)
    profiles = ProfileRepository(database)
    activity_modes = ActivityModeRepository(database)
    desk = FakeDesk()
    runtime = RuntimeState()
    container = AppContainer(settings=settings, runtime=runtime, task_manager=TaskManager(), database=database, profiles=profiles, activity_modes=activity_modes, dashboard=DashboardService(desk, profiles), mqtt=object(), height_monitor=object(), relay=object(), desk=desk)  # type: ignore[arg-type]
    application = create_application(settings=settings, container=container)
    await database.start()
    runtime.mark(runtime_status, "unrelated lifecycle state")

    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/status")
        assert response.status_code == 200
        assert response.json()["height"] == {"heightCm": 90.0, "observedAt": "2026-08-08T00:00:00Z", "status": "ONLINE", "provenance": None}
        assert response.json()["targetHeightCm"] is None

        assert (await client.post(
            "/api/control", json={"action": "HOLD", "direction": "UP"}
        )).status_code == 200
        assert (await client.post(
            "/api/target", json={"action": "SET", "targetCm": 100}
        )).status_code == 200
        assert (await client.post(
            "/api/control", json={"action": "STOP"}
        )).status_code == 200
        assert (await client.post(
            "/api/target", json={"action": "CANCEL"}
        )).status_code == 200

        created = await client.post("/api/profiles", json={"name": " 홍길동 ", "sittingHeightCm": 80.0, "standingHeightCm": 105.0, "ledColor": "ff3000", "ledBrightness": 180})
        assert created.status_code == 201
        assert created.json()["name"] == "홍길동"
        assert created.json()["ledColor"] == "FF3000"
        assert created.json()["ledBrightness"] == 180
        profile_id = created.json()["id"]

        modes = await client.get(f"/api/profiles/{profile_id}/activity-modes")
        assert modes.status_code == 200
        assert modes.json() == [{
            "key": "default", "kind": "DEFAULT", "name": "기본",
            "sittingHeightCm": 80.0, "standingHeightCm": 105.0,
            "ledColor": "FF3000", "ledBrightness": 180,
            "ledSchedule": None, "tiltLevel": None, "description": None,
            "editable": False,
        }]
        custom = await client.post(
            f"/api/profiles/{profile_id}/activity-modes",
            json={"name": " 독서 ", "sittingHeightCm": 82, "standingHeightCm": 108,
                  "ledColor": "ffd080", "ledBrightness": 40},
        )
        assert custom.status_code == 201
        assert custom.json()["kind"] == "CUSTOM"
        assert custom.json()["name"] == "독서"
        assert custom.json()["ledBrightness"] == 40
        # 모드는 높이를 정하지 않으므로 프로필 높이가 실려 나온다.
        assert custom.json()["sittingHeightCm"] == 80.0
        assert custom.json()["standingHeightCm"] == 105.0
        mode_id = custom.json()["key"]
        assert (await client.post(
            f"/api/profiles/{profile_id}/activity-modes",
            json={"name": "독서", "sittingHeightCm": 82, "standingHeightCm": 108},
        )).status_code == 409
        # 높이는 프로필이 소유한다. 모드로 높이를 고치려 해도 프로필 높이가 남는다.
        assert (await client.patch(
            f"/api/activity-modes/{mode_id}", json={"standingHeightCm": 109}
        )).json()["standingHeightCm"] == 105.0
        assert (await client.patch(
            f"/api/activity-modes/{mode_id}", json={"unknown": True}
        )).status_code == 422
        # 밝기만 따로 고칠 수 있고, 범위를 벗어나면 거부한다.
        assert (await client.patch(
            f"/api/activity-modes/{mode_id}", json={"ledBrightness": 255}
        )).json()["ledBrightness"] == 255
        assert (await client.patch(
            f"/api/activity-modes/{mode_id}", json={"ledBrightness": 256}
        )).status_code == 422
        assert (await client.delete(f"/api/activity-modes/{mode_id}")).status_code == 204
        assert (await client.delete(f"/api/activity-modes/{mode_id}")).status_code == 404

        updated = await client.patch(f"/api/profiles/{profile_id}", json={"ledColor": None})
        assert updated.status_code == 200
        assert updated.json()["ledColor"] is None
        assert (await client.get("/api/profiles")).json()[0]["id"] == profile_id
        assert (await client.delete(f"/api/profiles/{profile_id}")).status_code == 204
        assert (await client.get(f"/api/profiles/{profile_id}")).status_code == 404
        assert desk.commands == ["hold_up", "set_target", "stop_motion", "stop_motion"]

        assert (await client.post("/api/target", json={"action": "SET", "targetCm": 116})).status_code == 422
        assert (await client.post("/api/control", json={"action": "HOLD", "direction": "UP", "unknown": True})).status_code == 422
        desk.reject = True
        assert (await client.post("/api/control", json={"action": "HOLD", "direction": "UP"})).status_code == 409
        desk.reject = False
        desk.unavailable = True
        assert (await client.post("/api/control", json={"action": "HOLD", "direction": "UP"})).status_code == 503

    await database.stop()


async def test_storage_routes_return_503_only_when_database_is_not_ready(tmp_path) -> None:
    settings = Settings(environment="test", storage=StorageSettings(database_path=tmp_path / "desk.db"), dashboard=DashboardSettings(serve_frontend=False), _env_file=None)
    database = SQLiteDatabase(settings.storage.database_path)
    profiles = ProfileRepository(database)
    activity_modes = ActivityModeRepository(database)
    desk = FakeDesk()
    runtime = RuntimeState()
    container = AppContainer(settings=settings, runtime=runtime, task_manager=TaskManager(), database=database, profiles=profiles, activity_modes=activity_modes, dashboard=DashboardService(desk, profiles), mqtt=object(), height_monitor=object(), relay=object(), desk=desk)  # type: ignore[arg-type]
    application = create_application(settings=settings, container=container)
    runtime.mark(ApplicationStatus.FAILED, "database has not started")

    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.get("/api/profiles")).status_code == 503
        assert (await client.get("/api/profiles/profile-a/activity-modes")).status_code == 503
