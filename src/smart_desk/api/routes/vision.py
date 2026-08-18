"""Vision 관측 상태와 메모리 기반 디버그 frame API."""

import cv2
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from smart_desk.modules.vision import get_vision
from smart_desk.modules.vision.models import (
    IdentityResponse,
    VisionDebugResponse,
    VisionStatusResponse,
)
from smart_desk.core.container import get_container


router = APIRouter(prefix="/api/vision", tags=["vision"])


@router.get("/status", response_model=VisionStatusResponse)
async def get_vision_status() -> VisionStatusResponse:
    """Vision 시작 전·종료 후에도 fail-closed snapshot을 반환한다."""

    base = get_vision().get_status()
    identity_service = get_container().identity
    if identity_service is None:
        return base
    observation = identity_service.identity()
    identity = IdentityResponse(
        status=observation.status,
        profile_id=observation.profile_id,
        observed_at=observation.observed_at,
        expires_at=observation.expires_at,
    )
    return base.model_copy(update={"identity": identity})


@router.get("/debug", response_model=VisionDebugResponse)
async def get_vision_debug() -> VisionDebugResponse:
    """추론 geometry와 frame 준비 상태만 반환한다. image bytes는 아래 endpoint다."""

    return get_vision().get_debug()


@router.get("/debug/frame/{camera}")
async def get_vision_debug_frame(camera: str) -> Response:
    """마지막 성공 추론의 원본 JPEG 1장을 반환한다. 영상 스트리밍이나 저장은 하지 않는다."""

    if camera not in {"upper", "lower"}:
        raise HTTPException(status_code=404, detail="unknown vision camera")
    vision = get_vision()
    get_remote_frame = getattr(vision, "get_debug_frame_bytes", None)
    if callable(get_remote_frame):
        payload = await get_remote_frame(camera)
        if payload is None:
            raise HTTPException(status_code=404, detail="no inferred frame available")
        return Response(
            content=payload,
            media_type="image/jpeg",
            headers={"Cache-Control": "no-store, max-age=0"},
        )
    frame = vision.get_debug_frame(camera)
    if frame is None:
        raise HTTPException(status_code=404, detail="no inferred frame available")
    encoded, payload = cv2.imencode(".jpg", frame, (cv2.IMWRITE_JPEG_QUALITY, 85))
    if not encoded:
        raise HTTPException(status_code=503, detail="could not encode vision debug frame")
    return Response(
        content=payload.tobytes(),
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store, max-age=0"},
    )
