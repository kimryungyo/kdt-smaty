"""원격 Vision 보조 요청과 distinct frame 연속성 테스트."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx

from smart_desk.config.settings import VisionClientSettings
from smart_desk.modules.vision.models import (
    AssociationResponse,
    CameraStatus,
    CameraStatusResponse,
    IdentityResponse,
    PostureResponse,
    PostureStatus,
    PresenceResponse,
    PresenceStatus,
    VisionStatusResponse,
)
from smart_desk.modules.vision.remote import RemoteVisionService


NOW = datetime(2026, 8, 21, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.value = 10.0

    def __call__(self) -> float:
        return self.value


class FakeClient:
    def __init__(self, result: VisionStatusResponse) -> None:
        self.result = result

    async def __aenter__(self) -> "FakeClient":
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def post(self, url: str, **_kwargs: object) -> httpx.Response:
        return httpx.Response(
            200,
            json=self.result.model_dump(mode="json", by_alias=True),
            request=httpx.Request("POST", url),
        )

    async def get(self, url: str, **_kwargs: object) -> httpx.Response:
        return httpx.Response(503, request=httpx.Request("GET", url))


def status(observed_at: datetime = NOW) -> VisionStatusResponse:
    camera = CameraStatusResponse(status=CameraStatus.ONLINE, observed_at=observed_at)
    return VisionStatusResponse(
        cameras={"upper": camera, "lower": camera},
        identity=IdentityResponse(),
        presence=PresenceResponse(
            raw_status=PresenceStatus.PRESENT_SINGLE,
            status=PresenceStatus.PRESENT_SINGLE,
            upper_count=1,
            lower_count=1,
            observed_at=observed_at,
        ),
        posture=PostureResponse(
            raw_status=PostureStatus.SITTING,
            status=PostureStatus.SITTING,
            observed_at=observed_at,
        ),
        association=AssociationResponse(usable=True, reason_codes=[]),
    )


async def test_auxiliary_endpoint_failures_do_not_discard_automation_status() -> None:
    clock = Clock()
    result = status()
    service = RemoteVisionService(
        VisionClientSettings(),
        monotonic=clock,
        utc_now=lambda: NOW,
        http_client_factory=lambda: FakeClient(result),  # type: ignore[arg-type]
    )

    await service.process_once()

    snapshot = service.get_snapshot()
    assert snapshot.usable is True
    assert snapshot.stable_presence is PresenceStatus.PRESENT_SINGLE
    assert snapshot.stable_posture is PostureStatus.SITTING
    assert service.get_fresh_face_observation() is None


def test_remote_capture_marker_advances_only_for_a_distinct_remote_frame() -> None:
    clock = Clock()
    service = RemoteVisionService(
        VisionClientSettings(), monotonic=clock, utc_now=lambda: NOW
    )

    first = service._from_response(status())
    clock.value += 1
    duplicate = service._from_response(status())
    clock.value += 1
    distinct = service._from_response(status(NOW + timedelta(seconds=1)))

    assert duplicate.upper.captured_monotonic == first.upper.captured_monotonic
    assert duplicate.lower.captured_monotonic == first.lower.captured_monotonic
    assert distinct.upper.captured_monotonic > duplicate.upper.captured_monotonic
    assert distinct.lower.captured_monotonic > duplicate.lower.captured_monotonic
