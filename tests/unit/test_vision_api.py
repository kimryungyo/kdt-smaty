"""Vision HTTP 응답의 공개 JSON 경계를 검증한다."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from types import SimpleNamespace

from smart_desk.api.routes import vision as vision_route
from smart_desk.modules.vision.models import (
    AssociationResponse,
    CameraStatus,
    CameraStatusResponse,
    IdentityResponse,
    IdentityStatus,
    PostureResponse,
    PostureStatus,
    PresenceResponse,
    PresenceStatus,
    VisionStatusResponse,
)


class FakeVision:
    def get_status(self) -> VisionStatusResponse:
        return VisionStatusResponse(
            cameras={
                "upper": CameraStatusResponse(status=CameraStatus.STALE, age_seconds=1.5),
                "lower": CameraStatusResponse(status=CameraStatus.OFFLINE),
            },
            identity=IdentityResponse(status=IdentityStatus.UNKNOWN),
            presence=PresenceResponse(
                raw_status=PresenceStatus.UNKNOWN,
                status=PresenceStatus.UNKNOWN,
            ),
            posture=PostureResponse(
                raw_status=PostureStatus.UNKNOWN,
                status=PostureStatus.UNKNOWN,
            ),
            association=AssociationResponse(usable=False, reason_codes=[]),
        )


def test_vision_status_is_camel_case_and_hides_raw_frames(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    application = FastAPI()
    application.include_router(vision_route.router)
    monkeypatch.setattr(vision_route, "get_vision", lambda: FakeVision())
    monkeypatch.setattr(vision_route, "get_container", lambda: SimpleNamespace(identity=None))

    response = TestClient(application).get("/api/vision/status")

    assert response.status_code == 200
    body = response.json()
    assert body["identity"] == {
        "status": "UNKNOWN",
        "profileId": None,
        "observedAt": None,
        "expiresAt": None,
    }
    assert body["cameras"]["upper"]["ageSeconds"] == 1.5
    assert "rawStatus" in body["presence"]
    assert "raw_status" not in str(body)
    assert "frame" not in str(body).lower()
