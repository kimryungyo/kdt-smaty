"""Stateless HTTP entrypoint for WebRTC Vision inference."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
import logging
from typing import AsyncIterator

import cv2
from fastapi import FastAPI, Header, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field
import uvicorn

from smart_desk.config.settings import Settings, get_settings
from smart_desk.core.logging import configure_logging
from smart_desk.modules.media import MjpegFrameSource, WebRtcFrameSource
from smart_desk.modules.vision.detector import (
    CompositeVisionDetector,
    NoopVisionDetector,
    OpenCvYoloPoseLowerDetector,
    OpenCvYuNetUpperDetector,
    PresenceAndFaceUpperDetector,
)
from smart_desk.modules.vision.service import VisionService
from smart_desk.modules.vision.models import VisionDebugResponse, VisionStatusResponse


LOGGER = logging.getLogger(__name__)


class FaceEmbeddingResponse(BaseModel):
    """Internal Main-to-Vision response; vectors are never persisted or public."""

    model_config = ConfigDict(alias_generator=lambda value: value.split("_")[0] + "".join(part.title() for part in value.split("_")[1:]), populate_by_name=True)
    face_count: int
    captured_monotonic: float | None = None
    observed_at: datetime | None = None
    embedding: list[float] | None = None


class AnalyzeRequest(BaseModel):
    """Small control-plane request; frame bytes are always read through WHEP."""

    model_config = ConfigDict(extra="forbid")
    schema_version: int = Field(default=1, alias="schemaVersion")


class VisionWorker:
    """Owns camera readers and models without database or disk state."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._upper = MjpegFrameSource(
            name="user",
            stream_url=settings.media.user.receive_url,
            reconnect_interval_seconds=settings.media.reconnect_interval_seconds,
        )
        self._lower = (
            WebRtcFrameSource(
                name="posture",
                whep_url=settings.media.posture.receive_url,
                reconnect_interval_seconds=settings.media.reconnect_interval_seconds,
            )
            if settings.media.posture.receive_enabled
            else None
        )
        self._vision = VisionService(
            upper_source=self._upper,
            lower_source=self._lower,
            detector=_build_detector(settings),
            settings=settings.vision,
        )
        self._embedding_extractor = None
        if settings.face.embedding_model_path is not None:
            try:
                from smart_desk.modules.identity.opencv import OpenCvSFaceEmbeddingExtractor
                self._embedding_extractor = OpenCvSFaceEmbeddingExtractor(
                    settings.face.embedding_model_path,
                    min_face_size=settings.face.min_face_size,
                    min_blur_variance=settings.face.min_blur_variance,
                    min_brightness=settings.face.min_brightness,
                    max_brightness=settings.face.max_brightness,
                )
            except Exception:
                LOGGER.exception("Vision SFace model load failed")

    async def start(self) -> None:
        await self._upper.start()
        if self._lower is not None:
            await self._lower.start()

    async def stop(self) -> None:
        await self._vision.stop()
        if self._lower is not None:
            await self._lower.stop()
        await self._upper.stop()

    async def analyze(self) -> VisionStatusResponse:
        await self._vision.process_once()
        return self._vision.get_status()

    async def face_embedding(self) -> FaceEmbeddingResponse:
        observation = self._vision.get_fresh_face_observation()
        if observation is None:
            return FaceEmbeddingResponse(face_count=0)
        vector = None
        if len(observation.boxes) == 1 and self._embedding_extractor is not None:
            vector = await asyncio.to_thread(self._embedding_extractor.extract, observation)
        return FaceEmbeddingResponse(
            face_count=len(observation.boxes),
            captured_monotonic=observation.captured_monotonic,
            observed_at=observation.observed_at,
            embedding=None if vector is None else list(vector),
        )


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
                min_person_confidence=settings.vision.upper_presence_min_person_confidence,
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
        # The standalone vision service can run without a server-side token
        # configuration.  Keep authentication optional in that deployment.
        token = getattr(getattr(resolved, "vision_server", None), "api_token", None)
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

    @app.get("/v1/debug", response_model=VisionDebugResponse)
    async def debug(authorization: str | None = Header(default=None)) -> VisionDebugResponse:
        authorize(authorization)
        return worker._vision.get_debug()

    @app.get("/v1/face-embedding", response_model=FaceEmbeddingResponse)
    async def face_embedding(authorization: str | None = Header(default=None)) -> FaceEmbeddingResponse:
        authorize(authorization)
        return await worker.face_embedding()

    @app.get("/v1/debug/frame/{camera}")
    async def debug_frame(camera: str, authorization: str | None = Header(default=None)) -> Response:
        authorize(authorization)
        if camera not in {"upper", "lower"}:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unknown camera")
        frame = worker._vision.get_debug_frame(camera)
        if frame is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no inferred frame available")
        encoded, payload = cv2.imencode(
            ".jpg", frame, (cv2.IMWRITE_JPEG_QUALITY, 85)
        )
        if not encoded:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="could not encode frame")
        return Response(content=payload.tobytes(), media_type="image/jpeg", headers={"Cache-Control": "no-store, max-age=0"})

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
