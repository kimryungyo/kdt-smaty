"""실제 TiltController의 상태 조회와 단계 이동 HTTP route다."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import Field

from smart_desk.core.container import get_container
from smart_desk.modules.dashboard.models import DashboardModel
from smart_desk.modules.tilt.controller import TiltCommandRejectedError


router = APIRouter(prefix="/api/tilt", tags=["tilt"])


class TiltStatusResponse(DashboardModel):
    status: str
    level: int | None = None
    target_level: int | None = None
    position_mm: float | None = None
    position_valid: bool = False
    min_level: int
    max_level: int
    detail: str
    last_error: str | None = None
    updated_at: datetime


class TiltTargetRequest(DashboardModel):
    level: int = Field(strict=True, ge=0)


def _unavailable() -> TiltStatusResponse:
    settings = get_container().settings.tilt
    return TiltStatusResponse(
        status="UNAVAILABLE",
        min_level=settings.min_level,
        max_level=settings.max_level,
        detail="틸팅 하드웨어가 아직 활성화되지 않았습니다.",
        updated_at=datetime.now(UTC),
    )


def _response() -> TiltStatusResponse:
    container = get_container()
    tilt = container.tilt
    if tilt is None:
        return _unavailable()
    snapshot = tilt.get_snapshot()
    return TiltStatusResponse(
        status=snapshot.state,
        level=snapshot.level,
        target_level=snapshot.target_level,
        position_mm=snapshot.position_mm,
        position_valid=snapshot.position_valid,
        min_level=container.settings.tilt.min_level,
        max_level=container.settings.tilt.max_level,
        detail=snapshot.detail,
        last_error=snapshot.last_error,
        updated_at=snapshot.updated_at,
    )


@router.get("/status", response_model=TiltStatusResponse)
async def get_tilt_status() -> TiltStatusResponse:
    """현재 실제 틸트 controller 상태와 서버 설정 범위를 반환한다."""

    return _response()


@router.put("/target", response_model=TiltStatusResponse)
async def set_tilt_target(request: TiltTargetRequest) -> TiltStatusResponse:
    """설정 범위 안의 단계 이동을 요청한다."""

    container = get_container()
    settings = container.settings.tilt
    if not settings.min_level <= request.level <= settings.max_level:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"틸트 단계는 {settings.min_level}~{settings.max_level} 사이여야 합니다.",
        )
    tilt = container.tilt
    if tilt is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="틸팅 하드웨어가 아직 활성화되지 않았습니다.",
        )
    try:
        await tilt.set_target(request.level)
    except TiltCommandRejectedError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    return _response()


@router.post("/stop", response_model=TiltStatusResponse)
async def stop_tilt() -> TiltStatusResponse:
    """진행 중인 틸트 이동을 즉시 정지한다."""

    tilt = get_container().tilt
    if tilt is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="틸팅 하드웨어가 아직 활성화되지 않았습니다.",
        )
    await tilt.stop_motion("대시보드에서 틸팅을 정지했습니다.")
    return _response()
