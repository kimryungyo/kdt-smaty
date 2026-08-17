"""아직 장치가 없는 데스크 틸팅 HTTP 계약이다.

실제 actuator가 도입되면 이 route의 공개 모델은 유지하고 service를 연결한다.
현재는 브라우저가 추측으로 성공 상태를 표시하거나 localStorage 값을 장치 상태로
오인하지 않게 명시적으로 사용할 수 없음을 반환한다.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import Field

from smart_desk.modules.dashboard.models import DashboardModel


router = APIRouter(prefix="/api/tilt", tags=["tilt"])


class TiltStatusResponse(DashboardModel):
    status: str = "UNAVAILABLE"
    level: int | None = None
    target_level: int | None = None
    min_level: int = 0
    max_level: int = 5
    detail: str = "틸팅 하드웨어가 아직 연결되지 않았습니다."
    last_error: str | None = None
    updated_at: datetime


class TiltTargetRequest(DashboardModel):
    level: int = Field(strict=True, ge=0, le=5)


def _unavailable() -> TiltStatusResponse:
    return TiltStatusResponse(updated_at=datetime.now(UTC))


@router.get("/status", response_model=TiltStatusResponse)
async def get_tilt_status() -> TiltStatusResponse:
    """현재 구현 가능한 틸팅 상태를 반환한다."""

    return _unavailable()


@router.put("/target", response_model=TiltStatusResponse)
async def set_tilt_target(_request: TiltTargetRequest) -> TiltStatusResponse:
    """미구현 actuator 요청을 성공처럼 처리하지 않는다."""

    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="틸팅 하드웨어가 아직 연결되지 않았습니다.",
    )
