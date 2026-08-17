"""HTTP boundary for automation status and user-bound commands."""

from collections.abc import Awaitable, Callable
from typing import TypeVar

from fastapi import APIRouter, HTTPException, status

from smart_desk.modules.automation import get_automation
from smart_desk.modules.automation.models import (
    ActivityModeRequest, AutomationStatusResponse, ControlModeRequest,
)
from smart_desk.modules.automation.service import AutomationConflictError, AutomationNotFoundError
from smart_desk.modules.profiles.activity_modes import ActivityModeRepositoryError
from smart_desk.storage import StorageError


router = APIRouter(prefix="/api/automation", tags=["automation"])
desk_router = APIRouter(prefix="/api/desk", tags=["automation"])
Result = TypeVar("Result")


async def _run(operation: Callable[[], Awaitable[Result]]) -> Result:
    try:
        return await operation()
    except AutomationNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except AutomationConflictError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)) from error
    except (StorageError, ActivityModeRepositoryError, RuntimeError) as error:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="자동화 의존성을 현재 사용할 수 없습니다.") from error


@router.get("/status", response_model=AutomationStatusResponse)
async def automation_status() -> AutomationStatusResponse:
    return AutomationStatusResponse.from_snapshot(get_automation().get_snapshot())


@desk_router.put("/control-mode", response_model=AutomationStatusResponse)
async def set_control_mode(request: ControlModeRequest) -> AutomationStatusResponse:
    await _run(lambda: get_automation().set_control_mode(request.control_mode, request.expected_session_id))
    return AutomationStatusResponse.from_snapshot(get_automation().get_snapshot())


@desk_router.put("/activity-mode", response_model=AutomationStatusResponse)
async def set_activity_mode(request: ActivityModeRequest) -> AutomationStatusResponse:
    await _run(lambda: get_automation().set_activity_mode(request.activity_mode_key, request.expected_session_id))
    return AutomationStatusResponse.from_snapshot(get_automation().get_snapshot())
