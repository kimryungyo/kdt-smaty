"""작업 모드 사용 시간 집계 HTTP route다."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status

from smart_desk.core.container import get_container
from smart_desk.storage import StorageError


router = APIRouter(prefix="/api/activity-modes", tags=["activity-modes"])

MAX_DAYS = 31


@router.get("/usage")
async def get_activity_mode_usage(
    days: int = Query(default=7, ge=1, le=MAX_DAYS),
    profile_id: str | None = Query(default=None, alias="profileId"),
) -> dict:
    """최근 `days`일의 모드별·날짜별 사용 시간을 초 단위로 반환한다."""

    usage = get_container().mode_usage
    if usage is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="사용 기록 저장소를 사용할 수 없습니다.",
        )
    try:
        return await usage.summarize(days=days, profile_id=profile_id)
    except StorageError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="사용 기록 저장소를 현재 사용할 수 없습니다.",
        ) from error
