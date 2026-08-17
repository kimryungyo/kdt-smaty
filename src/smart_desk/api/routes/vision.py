"""Vision 관측 상태 API."""

from fastapi import APIRouter

from smart_desk.modules.vision import get_vision
from smart_desk.modules.vision.models import IdentityResponse, VisionStatusResponse
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
