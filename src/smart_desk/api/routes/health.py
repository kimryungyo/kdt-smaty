"""프로세스 생존과 요청 준비 상태를 제공한다."""

from datetime import datetime

from fastapi import APIRouter, Response, status
from pydantic import BaseModel

from smart_desk.core.container import get_container
from smart_desk.core.runtime import ApplicationStatus
from smart_desk.modules.assistant.memory import ProfileMemorySnapshot, ProfileMemoryStatus


router = APIRouter(prefix="/health", tags=["health"])


class HealthResponse(BaseModel):
    """애플리케이션 health endpoint의 공통 응답이다."""

    status: str
    application_status: ApplicationStatus
    detail: str
    updated_at: datetime


class ProfileMemoryHealthResponse(BaseModel):
    enabled: bool
    status: ProfileMemoryStatus
    detail: str


@router.get("/live", response_model=HealthResponse)
async def live() -> HealthResponse:
    """Python 프로세스와 FastAPI 요청 처리가 살아 있음을 반환한다."""

    snapshot = get_container().runtime.snapshot()
    return HealthResponse(
        status="alive",
        application_status=snapshot.status,
        detail=snapshot.detail,
        updated_at=snapshot.updated_at,
    )


@router.get("/ready", response_model=HealthResponse)
async def ready(response: Response) -> HealthResponse:
    """필수 공유 자원이 요청을 처리할 준비가 됐는지 반환한다."""

    snapshot = get_container().runtime.snapshot()
    if not snapshot.ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthResponse(
        status="ready" if snapshot.ready else "not_ready",
        application_status=snapshot.status,
        detail=snapshot.detail,
        updated_at=snapshot.updated_at,
    )


@router.get("/profile-memory", response_model=ProfileMemoryHealthResponse)
async def profile_memory() -> ProfileMemoryHealthResponse:
    """Return optional Mem0 state without exposing memory content or provider details."""

    memory = get_container().profile_memory
    snapshot = (
        memory.snapshot()
        if memory is not None
        else ProfileMemorySnapshot(
            enabled=False,
            status=ProfileMemoryStatus.DISABLED,
            detail="profile memory가 구성되지 않았습니다.",
        )
    )
    return ProfileMemoryHealthResponse(
        enabled=snapshot.enabled, status=snapshot.status, detail=snapshot.detail
    )
