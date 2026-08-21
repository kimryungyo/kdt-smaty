"""profile 기본값과 custom 작업 모드 설정 HTTP route다."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TypeVar

from fastapi import APIRouter, HTTPException, Response, status

from smart_desk.core.container import get_container
from smart_desk.modules.automation.service import AutomationConflictError
from smart_desk.modules.profiles import get_activity_modes
from smart_desk.modules.profiles.activity_modes import (
    ActivityModeConflictError,
    ActivityModeNotFoundError,
    ActivityModeOwnershipError,
    ActivityModeRepositoryError,
)
from smart_desk.modules.profiles.models import (
    ActivityModeCreate,
    ActivityModeUpdate,
    EffectiveActivityMode,
)
from smart_desk.modules.profiles.repository import ProfileNotFoundError
from smart_desk.storage import StorageError


profiles_router = APIRouter(prefix="/api/profiles", tags=["activity-modes"])
activity_modes_router = APIRouter(prefix="/api/activity-modes", tags=["activity-modes"])
Result = TypeVar("Result")


async def _run(operation: Callable[[], Awaitable[Result]]) -> Result:
    try:
        return await operation()
    except (ProfileNotFoundError, ActivityModeNotFoundError) as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except ActivityModeOwnershipError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except (ActivityModeConflictError, AutomationConflictError) as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except (ActivityModeRepositoryError, StorageError) as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="작업 모드 저장소를 현재 사용할 수 없습니다.",
        ) from error


@profiles_router.get("/{profile_id}/activity-modes", response_model=list[EffectiveActivityMode])
async def list_activity_modes(profile_id: str) -> list[EffectiveActivityMode]:
    return await _run(lambda: get_activity_modes().list_effective_modes(profile_id))


@profiles_router.post(
    "/{profile_id}/activity-modes",
    response_model=EffectiveActivityMode,
    status_code=status.HTTP_201_CREATED,
)
async def create_activity_mode(
    profile_id: str, create: ActivityModeCreate
) -> EffectiveActivityMode:
    return await _run(lambda: get_activity_modes().create_mode(profile_id, create))


@activity_modes_router.patch("/{mode_id}", response_model=EffectiveActivityMode)
async def update_activity_mode(
    mode_id: str, update: ActivityModeUpdate
) -> EffectiveActivityMode:
    return await _run(lambda: get_activity_modes().update_mode(mode_id, update))


@activity_modes_router.delete("/{mode_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_activity_mode(mode_id: str) -> Response:
    automation = get_container().automation
    if automation is not None:
        await _run(lambda: automation.delete_activity_mode(mode_id))
    else:
        await _run(lambda: get_activity_modes().delete_mode(mode_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
