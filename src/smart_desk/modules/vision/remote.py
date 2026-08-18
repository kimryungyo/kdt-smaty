"""Main process adapter for the stateless Vision HTTP service."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
import math
import time
from typing import Callable

import httpx

from smart_desk.config.settings import VisionClientSettings
from pydantic import BaseModel, ConfigDict

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


class FaceEmbeddingResponse(BaseModel):
    model_config = ConfigDict(alias_generator=lambda value: value.split("_")[0] + "".join(part.title() for part in value.split("_")[1:]), validate_by_alias=True, validate_by_name=True)
    face_count: int
    captured_monotonic: float | None = None
    observed_at: datetime | None = None
    embedding: list[float] | None = None


@dataclass(frozen=True, slots=True)
class RemoteFaceObservation:
    boxes: tuple[object, ...]
    captured_monotonic: float
    observed_at: datetime
    embedding: tuple[float, ...] | None


class RemoteFaceEmbeddingExtractor:
    """Makes a Vision-produced SFace vector consumable by Main identity policy."""

    model_name = "opencv-sface"
    model_version = "2021dec"
    dimension = 128
    normalization = "l2"

    def extract(self, observation: RemoteFaceObservation) -> tuple[float, ...] | None:
        return observation.embedding


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
        self._debug = VisionDebugResponse(cameras={
            "upper": VisionDebugCameraResponse(error=self._last_error),
            "lower": VisionDebugCameraResponse(error=self._last_error),
        })
        self._face: RemoteFaceObservation | None = None

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
        self._face = None

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
                debug_response = await client.get(
                    f"{self._settings.base_url}/v1/debug", headers=headers
                )
                debug_response.raise_for_status()
                face_response = await client.get(
                    f"{self._settings.base_url}/v1/face-embedding", headers=headers
                )
                face_response.raise_for_status()
            status = VisionStatusResponse.model_validate(response.json())
            debug = VisionDebugResponse.model_validate(debug_response.json())
            face = FaceEmbeddingResponse.model_validate(face_response.json())
        except (httpx.HTTPError, ValueError) as error:
            self._last_error = type(error).__name__
            if self._expired():
                self._snapshot = self._unknown_snapshot(self._last_error)
            return
        self._last_success = self._monotonic()
        self._last_error = None
        self._snapshot = self._from_response(status)
        self._debug = debug
        self._face = self._face_from_response(face)

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
        return self._debug

    async def get_debug_frame_bytes(self, camera: str) -> bytes | None:
        if camera not in {"upper", "lower"}:
            return None
        headers: dict[str, str] = {}
        if self._settings.api_token is not None:
            headers["Authorization"] = f"Bearer {self._settings.api_token.get_secret_value()}"
        try:
            async with httpx.AsyncClient(timeout=self._settings.request_timeout_seconds) as client:
                response = await client.get(
                    f"{self._settings.base_url}/v1/debug/frame/{camera}", headers=headers
                )
                if response.status_code == 404:
                    return None
                response.raise_for_status()
                return response.content
        except httpx.HTTPError:
            return None

    def get_debug_frame(self, _camera: str):  # type: ignore[no-untyped-def]
        return None

    def get_fresh_face_observation(self) -> RemoteFaceObservation | None:
        if self._expired():
            return None
        return self._face

    @staticmethod
    def _face_from_response(response: FaceEmbeddingResponse) -> RemoteFaceObservation | None:
        if response.face_count != 1 or response.captured_monotonic is None or response.observed_at is None:
            return None
        vector = tuple(float(value) for value in response.embedding) if response.embedding is not None else None
        if vector is not None and (len(vector) != 128 or not all(math.isfinite(value) for value in vector)):
            vector = None
        return RemoteFaceObservation((object(),), response.captured_monotonic, response.observed_at, vector)

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
