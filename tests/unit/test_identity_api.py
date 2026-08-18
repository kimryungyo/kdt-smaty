import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from smart_desk.api.routes import identity as identity_route
from smart_desk.api.routes import profiles as profiles_route
from smart_desk.modules.identity.models import CurrentUserApi, CurrentUserResponse, SessionKind
from smart_desk.modules.identity.models import EnrollmentSnapshot, EnrollmentState
from smart_desk.modules.identity.service import (
    EnrollmentConflictError,
    FreshSingleFaceRequiredError,
    ModelUnavailableError,
)
from smart_desk.modules.profiles.repository import ProfileNotFoundError, ProfileRepositoryError


def test_current_user_response_is_camel_case_and_does_not_admit_sensitive_fields() -> None:
    response = CurrentUserResponse(
        session=CurrentUserApi(
            session_id="session-1",
            kind=SessionKind.ANONYMOUS,
            profile_id=None,
            started_at=datetime(2026, 1, 1, tzinfo=UTC),
            changed_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
    )
    payload = response.model_dump(by_alias=True)
    assert payload["session"]["sessionId"] == "session-1"
    assert not {"vector", "score", "threshold", "box", "image"} & set(payload["session"])
    with pytest.raises(ValidationError):
        CurrentUserApi.model_validate({**payload["session"], "vector": [1.0]})


class Profiles:
    async def get_profile(self, profile_id: str) -> object:
        if profile_id == "missing":
            raise ProfileNotFoundError("missing")
        return object()


class Identity:
    def __init__(self) -> None:
        self.error: Exception | None = None
        self.snapshot = EnrollmentSnapshot(
            enrollment_id="enrollment-1",
            profile_id="profile-1",
            state=EnrollmentState.WAITING_FACE,
            required_samples=3,
            accepted_samples=0,
            started_at=datetime(2026, 1, 1, tzinfo=UTC),
            changed_at=datetime(2026, 1, 1, tzinfo=UTC),
        )

    async def start_enrollment(self, _profile_id: str) -> EnrollmentSnapshot:
        if self.error:
            raise self.error
        return self.snapshot

    async def enrollment(self, enrollment_id: str) -> EnrollmentSnapshot | None:
        return self.snapshot if enrollment_id == self.snapshot.enrollment_id else None

    async def cancel(self, enrollment_id: str) -> bool:
        if self.error:
            raise self.error
        return enrollment_id == self.snapshot.enrollment_id

    async def delete_face(self, _profile_id: str) -> bool:
        if self.error:
            raise self.error
        return True


def test_enrollment_endpoints_use_contract_statuses_and_hide_sensitive_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = Identity()
    application = FastAPI()
    application.include_router(identity_route.router)
    monkeypatch.setattr(identity_route, "get_identity", lambda: identity)
    monkeypatch.setattr(identity_route, "get_current_user", lambda: SimpleNamespace(snapshot=_none))
    monkeypatch.setattr(
        identity_route,
        "get_container",
        lambda: SimpleNamespace(profiles=Profiles()),
    )
    client = TestClient(application)

    created = client.post("/api/profiles/profile-1/face-enrollments")
    assert created.status_code == 202
    assert created.json()["enrollmentId"] == "enrollment-1"
    assert not {"image", "box", "vector", "score", "threshold"} & set(created.json())
    assert client.get("/api/face-enrollments/enrollment-1").status_code == 200
    assert client.delete("/api/face-enrollments/enrollment-1").status_code == 204
    assert client.delete("/api/profiles/profile-1/face").status_code == 204
    assert client.get("/api/face-enrollments/missing").status_code == 404
    assert client.post("/api/profiles/missing/face-enrollments").status_code == 404

    for error, expected in (
        (ModelUnavailableError("MODEL_UNAVAILABLE"), 503),
        (FreshSingleFaceRequiredError("FRESH_SINGLE_FACE_REQUIRED"), 503),
        (EnrollmentConflictError("ENROLLMENT_IN_PROGRESS"), 409),
    ):
        identity.error = error
        assert client.post("/api/profiles/profile-1/face-enrollments").status_code == expected
    identity.error = EnrollmentConflictError("ENROLLMENT_NOT_CANCELLABLE")
    assert client.delete("/api/face-enrollments/enrollment-1").status_code == 409


async def _none():
    return None


class _NoPinProfiles:
    """PIN이 걸리지 않은 프로필 저장소 stub이다."""

    async def get_pin_hash(self, _profile_id: str) -> str | None:
        return None


async def test_profile_delete_storage_failure_aborts_identity_suspension(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class IdentityMutation:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def prepare_profile_delete(self, profile_id: str) -> None:
            self.calls.append(f"prepare:{profile_id}")

        async def abort_profile_delete(self, profile_id: str) -> None:
            self.calls.append(f"abort:{profile_id}")

        async def finalize_profile_delete(self, profile_id: str) -> None:
            self.calls.append(f"finalize:{profile_id}")

    class Dashboard:
        async def get_profile(self, _profile_id: str) -> object:
            return object()

        async def delete_profile(self, _profile_id: str) -> None:
            raise ProfileRepositoryError("storage unavailable")

    identity = IdentityMutation()
    monkeypatch.setattr(
        profiles_route, "get_container",
        lambda: SimpleNamespace(
            runtime=SimpleNamespace(snapshot=lambda: SimpleNamespace(ready=True)),
            identity=identity,
            profile_memory=None,
            profiles=_NoPinProfiles(),
            current_user=None,
        ),
    )
    monkeypatch.setattr(profiles_route, "get_dashboard", lambda: Dashboard())

    with pytest.raises(HTTPException) as raised:
        await profiles_route.delete_profile("profile-1")
    assert raised.value.status_code == 503
    assert identity.calls == ["prepare:profile-1", "abort:profile-1"]


async def test_profile_delete_cancellation_aborts_identity_suspension(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class IdentityMutation:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def prepare_profile_delete(self, profile_id: str) -> None:
            self.calls.append(f"prepare:{profile_id}")

        async def abort_profile_delete(self, profile_id: str) -> None:
            self.calls.append(f"abort:{profile_id}")

        async def finalize_profile_delete(self, profile_id: str) -> None:
            self.calls.append(f"finalize:{profile_id}")

    class Dashboard:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.pending: asyncio.Future[None] = asyncio.get_running_loop().create_future()

        async def get_profile(self, _profile_id: str) -> object:
            return object()

        async def delete_profile(self, _profile_id: str) -> None:
            self.started.set()
            await self.pending

    identity = IdentityMutation()
    dashboard = Dashboard()
    monkeypatch.setattr(
        profiles_route, "get_container",
        lambda: SimpleNamespace(
            runtime=SimpleNamespace(snapshot=lambda: SimpleNamespace(ready=True)),
            identity=identity,
            profile_memory=None,
            profiles=_NoPinProfiles(),
            current_user=None,
        ),
    )
    monkeypatch.setattr(profiles_route, "get_dashboard", lambda: dashboard)

    deletion = asyncio.create_task(profiles_route.delete_profile("profile-1"))
    await dashboard.started.wait()
    dashboard.pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await deletion
    assert identity.calls == ["prepare:profile-1", "abort:profile-1"]


async def test_profile_memory_delete_precedes_identity_and_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    class Memory:
        async def delete_profile(self, profile_id: str) -> None: calls.append(f"memory:{profile_id}")
    class IdentityMutation:
        async def prepare_profile_delete(self, profile_id: str) -> None: calls.append(f"prepare:{profile_id}")
        async def abort_profile_delete(self, profile_id: str) -> None: calls.append(f"abort:{profile_id}")
        async def finalize_profile_delete(self, profile_id: str) -> None: calls.append(f"finalize:{profile_id}")
    class Dashboard:
        async def get_profile(self, profile_id: str) -> object: calls.append(f"exists:{profile_id}"); return object()
        async def delete_profile(self, profile_id: str) -> None: calls.append(f"db:{profile_id}")
    monkeypatch.setattr(profiles_route, "get_dashboard", lambda: Dashboard())
    monkeypatch.setattr(profiles_route, "get_container", lambda: SimpleNamespace(identity=IdentityMutation(), profile_memory=Memory(), profiles=_NoPinProfiles(), current_user=None))
    await profiles_route.delete_profile("profile-1")
    assert calls == ["exists:profile-1", "memory:profile-1", "prepare:profile-1", "db:profile-1", "finalize:profile-1"]


async def test_profile_memory_failure_preserves_identity_and_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class Memory:
        async def delete_profile(self, _profile_id: str) -> None:
            from smart_desk.modules.assistant.memory import ProfileMemoryError

            raise ProfileMemoryError("profile_memory_delete_failed")

    class IdentityMutation:
        async def prepare_profile_delete(self, _profile_id: str) -> None:
            calls.append("prepare")

    class Dashboard:
        async def get_profile(self, _profile_id: str) -> object:
            calls.append("exists")
            return object()

        async def delete_profile(self, _profile_id: str) -> None:
            calls.append("db")

    monkeypatch.setattr(profiles_route, "get_dashboard", lambda: Dashboard())
    monkeypatch.setattr(
        profiles_route,
        "get_container",
        lambda: SimpleNamespace(identity=IdentityMutation(), profile_memory=Memory(), profiles=_NoPinProfiles(), current_user=None),
    )

    with pytest.raises(HTTPException) as raised:
        await profiles_route.delete_profile("profile-1")

    assert raised.value.status_code == 503
    assert calls == ["exists"]
