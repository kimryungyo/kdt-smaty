"""Vision HTTP 응답의 공개 JSON 경계를 검증한다."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from types import SimpleNamespace
import numpy as np

from smart_desk.api.routes import vision as vision_route
from smart_desk.modules.vision.models import (
    AssociationResponse,
    CameraStatus,
    CameraStatusResponse,
    DebugBoxResponse,
    VisionDebugCameraResponse,
    VisionDebugResponse,
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

    def get_debug(self) -> VisionDebugResponse:
        return VisionDebugResponse(
            cameras={
                "upper": VisionDebugCameraResponse(
                    frame_width=4,
                    frame_height=3,
                    person_boxes=[DebugBoxResponse(x=0, y=0, width=3, height=2, confidence=.8)],
                    frame_available=True,
                ),
                "lower": VisionDebugCameraResponse(),
            }
        )

    def get_debug_frame(self, camera: str):  # type: ignore[no-untyped-def]
        return np.zeros((3, 4, 3), dtype=np.uint8) if camera == "upper" else None


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


def test_vision_debug_geometry_and_inferred_jpeg_frame_are_separate(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    application = FastAPI()
    application.include_router(vision_route.router)
    monkeypatch.setattr(vision_route, "get_vision", lambda: FakeVision())

    client = TestClient(application)
    debug = client.get("/api/vision/debug")
    frame = client.get("/api/vision/debug/frame/upper")

    assert debug.status_code == 200
    assert debug.json()["cameras"]["upper"]["personBoxes"][0]["confidence"] == .8
    assert "debugFrame" not in str(debug.json())
    assert frame.status_code == 200
    assert frame.headers["content-type"] == "image/jpeg"
    assert frame.content.startswith(b"\xff\xd8")
    assert client.get("/api/vision/debug/frame/unknown").status_code == 404
