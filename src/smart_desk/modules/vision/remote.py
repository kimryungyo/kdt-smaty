"""Main process adapter for the stateless Vision HTTP service."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import time
from typing import Callable

import httpx

from smart_desk.config.settings import VisionClientSettings
from smart_desk.modules.vision.models import (
    BlockCode,
    CameraObservation,
    CameraStatus,
    PostureStatus,
    VisionDebugCameraResponse,
    VisionDebugResponse,
    VisionSnapshot,
    VisionStatusResponse,
)
from smart_desk.modules.vision.models import PresenceStatus


def _utc_now() -> datetime:
    return datetime.now(UTC)


class RemoteVisionService:
    """Polls Vision on behalf of Main and exposes the existing snapshot surface.

    The object intentionally stores only the latest decoded response.  It never
    receives frame bytes, and a failed/late request immediately becomes an
    unusable snapshot after the configured freshness window.
    """

    def __init__(
        self,
        settings: VisionClientSettings,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        utc_now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._settings = settings
        self._monotonic = monotonic
        self._utc_now = utc_now
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._last_success: float | None = None
        self._last_error: str | None = "vision service has not replied"
        self._snapshot = self._unknown_snapshot(self._last_error)

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self._run(), name="remote-vision-poll")

    async def stop(self) -> None:
        self._stop.set()
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._last_success = None
        self._last_error = "vision service stopped"
        self._snapshot = self._unknown_snapshot(self._last_error)

    async def _run(self) -> None:
        while not self._stop.is_set():
            await self.process_once()
            try:
                await asyncio.wait_for(self._stop.wait(), self._settings.poll_interval_seconds)
            except TimeoutError:
                pass

    async def process_once(self) -> None:
        headers: dict[str, str] = {}
        if self._settings.api_token is not None:
            headers["Authorization"] = (
                f"Bearer {self._settings.api_token.get_secret_value()}"
            )
        try:
            async with httpx.AsyncClient(timeout=self._settings.request_timeout_seconds) as client:
                response = await client.post(
                    f"{self._settings.base_url}/v1/analyze",
                    json={"schemaVersion": 1},
                    headers=headers,
                )
                response.raise_for_status()
            status = VisionStatusResponse.model_validate(response.json())
        except (httpx.HTTPError, ValueError) as error:
            self._last_error = type(error).__name__
            if self._expired():
                self._snapshot = self._unknown_snapshot(self._last_error)
            return
        self._last_success = self._monotonic()
        self._last_error = None
        self._snapshot = self._from_response(status)

    def get_snapshot(self) -> VisionSnapshot:
        if self._expired():
            return self._unknown_snapshot(self._last_error or "vision result stale")
        return self._snapshot

    def get_status(self) -> VisionStatusResponse:
        snapshot = self.get_snapshot()
        now = self._utc_now()
        def camera(name: str, observation: CameraObservation):
            from smart_desk.modules.vision.models import CameraStatusResponse
            if not observation.connected:
                state = CameraStatus.ERROR if observation.error else CameraStatus.OFFLINE
            else:
                state = CameraStatus.ONLINE
            return CameraStatusResponse(
                status=state,
                observed_at=observation.observed_at,
                expires_at=now if not observation.connected else None,
                error=observation.error,
            )
        from smart_desk.modules.vision.models import (
            AssociationResponse,
            IdentityResponse,
            PostureResponse,
            PresenceResponse,
        )
        return VisionStatusResponse(
            cameras={"upper": camera("upper", snapshot.upper), "lower": camera("lower", snapshot.lower)},
            identity=IdentityResponse(),
            presence=PresenceResponse(
                raw_status=snapshot.raw_presence,
                status=snapshot.stable_presence,
                upper_count=snapshot.upper.count,
                lower_count=snapshot.lower.count,
                observed_at=snapshot.upper.observed_at,
            ),
            posture=PostureResponse(
                raw_status=snapshot.raw_posture,
                status=snapshot.stable_posture,
                observed_at=snapshot.lower.observed_at,
            ),
            association=AssociationResponse(
                usable=snapshot.usable, reason_codes=list(snapshot.reason_codes)
            ),
        )

    def get_debug(self) -> VisionDebugResponse:
        return VisionDebugResponse(
            cameras={
                "upper": VisionDebugCameraResponse(error=self._last_error),
                "lower": VisionDebugCameraResponse(error=self._last_error),
            }
        )

    def get_debug_frame(self, _camera: str):  # type: ignore[no-untyped-def]
        return None

    def get_fresh_face_observation(self):  # type: ignore[no-untyped-def]
        # Raw frames do not cross the container boundary.  Face identity remains
        # safely UNKNOWN until the embedding RPC is introduced.
        return None

    def _expired(self) -> bool:
        return self._last_success is None or (
            self._monotonic() - self._last_success > self._settings.request_timeout_seconds
        )

    def _from_response(self, response: VisionStatusResponse) -> VisionSnapshot:
        now_mono, now_wall = self._monotonic(), self._utc_now()
        upper_status, lower_status = response.cameras["upper"], response.cameras["lower"]
        upper = CameraObservation(
            upper_status.status is CameraStatus.ONLINE,
            now_mono,
            now_mono,
            upper_status.observed_at or now_wall,
            upper_status.error,
            response.presence.upper_count,
        )
        lower = CameraObservation(
            lower_status.status is CameraStatus.ONLINE,
            now_mono,
            now_mono,
            lower_status.observed_at or now_wall,
            lower_status.error,
            response.presence.lower_count,
            response.posture.raw_status,
        )
        return VisionSnapshot(
            upper=upper,
            lower=lower,
            raw_presence=response.presence.raw_status,
            stable_presence=response.presence.status,
            raw_posture=response.posture.raw_status,
            stable_posture=response.posture.status,
            presence_candidate_since=None,
            posture_candidate_since=response.posture.candidate_since,
            usable=response.association.usable,
            reason_codes=tuple(response.association.reason_codes),
        )

    def _unknown_snapshot(self, error: str | None) -> VisionSnapshot:
        observation = CameraObservation(False, None, None, None, error)
        return VisionSnapshot(
            upper=observation,
            lower=observation,
            raw_presence=PresenceStatus.UNKNOWN,
            stable_presence=PresenceStatus.UNKNOWN,
            raw_posture=PostureStatus.UNKNOWN,
            stable_posture=PostureStatus.UNKNOWN,
            presence_candidate_since=None,
            posture_candidate_since=None,
            usable=False,
            reason_codes=(BlockCode.UPPER_CAMERA_UNAVAILABLE, BlockCode.LOWER_CAMERA_UNAVAILABLE),
        )
