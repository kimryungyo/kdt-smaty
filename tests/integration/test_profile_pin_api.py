"""프로필 PIN 잠금의 HTTP 계약을 실제 SQLite로 검증한다.

핵심 규칙은 하나다. 얼굴로 인식된 본인은 PIN 없이 수정하고, 그 외에는
PIN이 있어야 수정·삭제할 수 있다.
"""

from datetime import UTC, datetime

from httpx import ASGITransport, AsyncClient
import pytest

from smart_desk.application import create_application
from smart_desk.config.settings import DashboardSettings, Settings, StorageSettings
from smart_desk.core.container import AppContainer
from smart_desk.core.runtime import RuntimeState
from smart_desk.core.task_manager import TaskManager
from smart_desk.modules.dashboard import DashboardService
from smart_desk.modules.desk.models import (
    DeskSnapshot,
    DeskState,
    HeightSnapshot,
    HeightStatus,
    RelayEvent,
    RelaySnapshot,
    RelayState,
)
from smart_desk.modules.identity.models import CurrentUserSnapshot, SessionKind
from smart_desk.modules.profiles import ActivityModeRepository, ProfileRepository
from smart_desk.storage import SQLiteDatabase


NOW = datetime(2026, 8, 18, tzinfo=UTC)


class FakeDesk:
    def get_snapshot(self) -> DeskSnapshot:
        return DeskSnapshot(
            state=DeskState.IDLE,
            height=HeightSnapshot(90.0, NOW, HeightStatus.ONLINE),
            relay=RelaySnapshot(RelayEvent.ONLINE, RelayState.STOP, "t", None, None, NOW, None),
            target_height_cm=None,
            direction=None,
            detail="ready",
            last_error=None,
            updated_at=NOW,
        )


class FakeCurrentUser:
    """현재 인식된 사용자만 흉내 낸다."""

    def __init__(self) -> None:
        self.profile_id: str | None = None

    async def snapshot(self) -> CurrentUserSnapshot | None:
        if self.profile_id is None:
            return None
        return CurrentUserSnapshot(
            session_id="session-test",
            kind=SessionKind.REGISTERED,
            profile_id=self.profile_id,
            started_at=NOW,
            changed_at=NOW,
        )


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
    desk = FakeDesk()
    current_user = FakeCurrentUser()
    container = AppContainer(
        settings=settings, runtime=RuntimeState(), task_manager=TaskManager(),
        database=database, profiles=profiles,
        activity_modes=ActivityModeRepository(database),
        dashboard=DashboardService(desk, profiles),
        mqtt=object(), height_monitor=object(), relay=object(), desk=desk,
    )  # type: ignore[arg-type]
    container.current_user = current_user  # type: ignore[assignment]
    application = create_application(settings=settings, container=container)
    await database.start()
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, current_user
    await database.stop()


async def _create(client: AsyncClient, name: str = "사용자") -> str:
    response = await client.post(
        "/api/profiles",
        json={"name": name, "sittingHeightCm": 80, "standingHeightCm": 105},
    )
    assert response.status_code == 201
    assert response.json()["hasPin"] is False
    return response.json()["id"]


async def test_pin_is_stored_hashed_and_never_returned(api) -> None:
    client, _ = api
    profile_id = await _create(client)

    assert (await client.put(f"/api/profiles/{profile_id}/pin", json={"pin": "1234"})).status_code == 204

    body = (await client.get(f"/api/profiles/{profile_id}")).json()
    assert body["hasPin"] is True
    assert "pin" not in body
    assert "pinHash" not in body


@pytest.mark.parametrize("pin", ["123", "12345", "12a4", ""])
async def test_invalid_pin_format_is_rejected(api, pin: str) -> None:
    client, _ = api
    profile_id = await _create(client)

    assert (await client.put(f"/api/profiles/{profile_id}/pin", json={"pin": pin})).status_code == 422


async def test_other_user_needs_pin_to_rename_or_delete(api) -> None:
    client, current_user = api
    profile_id = await _create(client)
    await client.put(f"/api/profiles/{profile_id}/pin", json={"pin": "1234"})
    current_user.profile_id = None  # 인식된 사용자가 없다.

    assert (await client.patch(f"/api/profiles/{profile_id}", json={"name": "몰래변경"})).status_code == 401
    assert (await client.patch(
        f"/api/profiles/{profile_id}", json={"name": "몰래변경"}, headers={"X-Profile-Pin": "9999"}
    )).status_code == 403
    assert (await client.delete(f"/api/profiles/{profile_id}")).status_code == 401

    renamed = await client.patch(
        f"/api/profiles/{profile_id}", json={"name": "정상변경"}, headers={"X-Profile-Pin": "1234"}
    )
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "정상변경"
    assert (await client.delete(
        f"/api/profiles/{profile_id}", headers={"X-Profile-Pin": "1234"}
    )).status_code == 204


async def test_recognized_owner_edits_without_pin(api) -> None:
    client, current_user = api
    profile_id = await _create(client)
    await client.put(f"/api/profiles/{profile_id}/pin", json={"pin": "1234"})
    current_user.profile_id = profile_id  # 얼굴로 인식된 본인이다.

    # 대시보드가 저장하는 높이·LED와 이름 변경 모두 PIN 없이 통과한다.
    assert (await client.patch(f"/api/profiles/{profile_id}", json={"standingHeightCm": 110})).status_code == 200
    assert (await client.patch(f"/api/profiles/{profile_id}", json={"name": "본인변경"})).status_code == 200


async def test_delete_needs_pin_even_for_the_recognized_owner(api) -> None:
    client, current_user = api
    profile_id = await _create(client)
    await client.put(f"/api/profiles/{profile_id}/pin", json={"pin": "1234"})
    current_user.profile_id = profile_id  # 본인이 인식돼 있어도 삭제는 막는다.

    assert (await client.delete(f"/api/profiles/{profile_id}")).status_code == 401
    assert (await client.delete(
        f"/api/profiles/{profile_id}", headers={"X-Profile-Pin": "9999"}
    )).status_code == 403
    assert (await client.delete(
        f"/api/profiles/{profile_id}", headers={"X-Profile-Pin": "1234"}
    )).status_code == 204


async def test_profile_without_pin_stays_open(api) -> None:
    client, current_user = api
    profile_id = await _create(client)
    current_user.profile_id = None

    assert (await client.patch(f"/api/profiles/{profile_id}", json={"name": "자유변경"})).status_code == 200
    assert (await client.post(f"/api/profiles/{profile_id}/pin/verify", json={"pin": "0000"})).status_code == 204


async def test_pin_change_requires_the_current_pin_when_not_the_owner(api) -> None:
    client, current_user = api
    profile_id = await _create(client)
    await client.put(f"/api/profiles/{profile_id}/pin", json={"pin": "1234"})
    current_user.profile_id = None

    assert (await client.put(f"/api/profiles/{profile_id}/pin", json={"pin": "5678"})).status_code == 401
    assert (await client.put(
        f"/api/profiles/{profile_id}/pin", json={"pin": "5678"}, headers={"X-Profile-Pin": "1234"}
    )).status_code == 204
    assert (await client.post(f"/api/profiles/{profile_id}/pin/verify", json={"pin": "5678"})).status_code == 204
    assert (await client.post(f"/api/profiles/{profile_id}/pin/verify", json={"pin": "1234"})).status_code == 403
