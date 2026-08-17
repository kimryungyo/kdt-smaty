"""Vision 관측 상태 API."""

from fastapi import APIRouter

from smart_desk.modules.vision import get_vision
from smart_desk.modules.vision.models import VisionStatusResponse


router = APIRouter(prefix="/api/vision", tags=["vision"])


@router.get("/status", response_model=VisionStatusResponse)
async def get_vision_status() -> VisionStatusResponse:
    """Vision 시작 전·종료 후에도 fail-closed snapshot을 반환한다."""

    return get_vision().get_status()
