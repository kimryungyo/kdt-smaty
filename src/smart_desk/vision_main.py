"""Stateless HTTP entrypoint for WebRTC Vision inference."""

from __future__ import annotations

from contextlib import asynccontextmanager
import logging
from typing import AsyncIterator

from fastapi import FastAPI, Header, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field
import uvicorn

from smart_desk.config.settings import Settings, get_settings
from smart_desk.core.logging import configure_logging
from smart_desk.modules.media import WebRtcFrameSource
from smart_desk.modules.vision.detector import (
    CompositeVisionDetector,
    NoopVisionDetector,
    OpenCvYoloPoseLowerDetector,
    OpenCvYuNetUpperDetector,
    PresenceAndFaceUpperDetector,
)
from smart_desk.modules.vision.service import VisionService
from smart_desk.modules.vision.models import VisionStatusResponse


LOGGER = logging.getLogger(__name__)


class AnalyzeRequest(BaseModel):
    """Small control-plane request; frame bytes are always read through WHEP."""

    model_config = ConfigDict(extra="forbid")
    schema_version: int = Field(default=1, alias="schemaVersion")


class VisionWorker:
    """Owns WHEP readers and models without database or disk state."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._upper = WebRtcFrameSource(
            name="user",
            whep_url=settings.media.user.receive_url,
            reconnect_interval_seconds=settings.media.reconnect_interval_seconds,
        )
        self._lower = WebRtcFrameSource(
            name="posture",
            whep_url=settings.media.posture.receive_url,
            reconnect_interval_seconds=settings.media.reconnect_interval_seconds,
        )
        self._vision = VisionService(
            upper_source=self._upper,
            lower_source=self._lower,
            detector=_build_detector(settings),
            settings=settings.vision,
        )

    async def start(self) -> None:
        await self._upper.start()
        await self._lower.start()

    async def stop(self) -> None:
        await self._vision.stop()
        await self._lower.stop()
        await self._upper.stop()

    async def analyze(self) -> VisionStatusResponse:
        await self._vision.process_once()
        return self._vision.get_status()


def _build_detector(settings: Settings):  # type: ignore[no-untyped-def]
    face_detector = NoopVisionDetector()
    upper_presence_detector = NoopVisionDetector()
    lower_detector = NoopVisionDetector()
    if settings.face.detector_model_path is not None:
        try:
            face_detector = OpenCvYuNetUpperDetector(
                settings.face.detector_model_path,
                score_threshold=settings.face.detector_score_threshold,
                nms_threshold=settings.face.detector_nms_threshold,
                min_face_size=settings.face.min_face_size,
            )
        except Exception:
            LOGGER.exception("Vision YuNet model load failed")
    if settings.vision.lower_pose_model_path is not None:
        try:
            upper_presence_detector = OpenCvYoloPoseLowerDetector(
                settings.vision.lower_pose_model_path,
                input_size=settings.vision.lower_pose_input_size,
                min_person_confidence=settings.vision.lower_pose_min_person_confidence,
                min_hip_confidence=settings.vision.lower_pose_min_hip_confidence,
                min_knee_ankle_confidence=settings.vision.lower_pose_min_knee_ankle_confidence,
                decision_threshold=settings.vision.lower_pose_decision_threshold,
            )
            lower_detector = OpenCvYoloPoseLowerDetector(
                settings.vision.lower_pose_model_path,
                input_size=settings.vision.lower_pose_input_size,
                min_person_confidence=settings.vision.lower_pose_min_person_confidence,
                min_hip_confidence=settings.vision.lower_pose_min_hip_confidence,
                min_knee_ankle_confidence=settings.vision.lower_pose_min_knee_ankle_confidence,
                decision_threshold=settings.vision.lower_pose_decision_threshold,
            )
        except Exception:
            LOGGER.exception("Vision pose model load failed")
    if not isinstance(upper_presence_detector, NoopVisionDetector) and not isinstance(face_detector, NoopVisionDetector):
        upper_detector = PresenceAndFaceUpperDetector(upper_presence_detector, face_detector)
    elif not isinstance(upper_presence_detector, NoopVisionDetector):
        upper_detector = upper_presence_detector
    else:
        upper_detector = face_detector
    if not isinstance(upper_detector, NoopVisionDetector) and not isinstance(lower_detector, NoopVisionDetector):
        return CompositeVisionDetector(upper_detector, lower_detector)
    if not isinstance(upper_detector, NoopVisionDetector):
        return upper_detector
    if not isinstance(lower_detector, NoopVisionDetector):
        return lower_detector
    return NoopVisionDetector()


def create_vision_application(settings: Settings | None = None) -> FastAPI:
    resolved = settings or get_settings()
    worker = VisionWorker(resolved)

    @asynccontextmanager
    async def lifespan(_application: FastAPI) -> AsyncIterator[None]:
        configure_logging(resolved.log_level)
        await worker.start()
        try:
            yield
        finally:
            await worker.stop()

    app = FastAPI(title="SMART DESK Vision", lifespan=lifespan)
    app.state.worker = worker

    def authorize(authorization: str | None) -> None:
        token = resolved.vision_server.api_token
        if token is None:
            return
        expected = f"Bearer {token.get_secret_value()}"
        if authorization != expected:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="unauthorized")

    @app.get("/health/live")
    async def live() -> dict[str, str]:
        return {"status": "alive"}

    @app.get("/health/ready")
    async def ready(response: Response) -> dict[str, str]:
        observation = worker._vision.get_status().association.usable
        if not observation:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            return {"status": "not_ready"}
        return {"status": "ready"}

    @app.post("/v1/analyze", response_model=VisionStatusResponse)
    async def analyze(
        request: AnalyzeRequest,
        authorization: str | None = Header(default=None),
    ) -> VisionStatusResponse:
        if request.schema_version != 1:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="unsupported schema")
        authorize(authorization)
        return await worker.analyze()

    return app


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        create_vision_application(settings),
        host=settings.vision_server.host,
        port=settings.vision_server.port,
        workers=1,
    )


if __name__ == "__main__":
    main()
