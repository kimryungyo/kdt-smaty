"""현재 책상 상태와 제어 명령을 위한 HTTP route다."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TypeVar

from fastapi import APIRouter, HTTPException, status

from smart_desk.modules.dashboard import get_dashboard
from smart_desk.modules.dashboard.models import (
    CancelTargetRequest,
    ControlRequest,
    DashboardStatusResponse,
    HoldControlRequest,
    SetTargetRequest,
    StopControlRequest,
    TargetRequest,
)
from smart_desk.modules.desk.controller import DeskCommandRejectedError
from smart_desk.storage import StorageError


router = APIRouter(prefix="/api", tags=["dashboard"])
Result = TypeVar("Result")


async def _run(operation: Callable[[], Awaitable[Result]]) -> Result:
    """도메인 오류를 공개 HTTP 의미로만 변환한다."""

    try:
        return await operation()
    except DeskCommandRejectedError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error
    except (RuntimeError, StorageError) as error:
        # controller의 비실행/STOP 발행 실패와 storage의 준비·손상 오류는
        # 클라이언트 입력이 아니라 일시적으로 사용할 수 없는 상태다.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="대시보드 의존성을 현재 사용할 수 없습니다.",
        ) from error


@router.get("/status", response_model=DashboardStatusResponse)
async def get_status() -> DashboardStatusResponse:
    """현재 Desk, height, relay snapshot을 반환한다."""

    return get_dashboard().get_status()


@router.post("/control", response_model=DashboardStatusResponse)
async def control(command: ControlRequest) -> DashboardStatusResponse:
    """수동 HOLD 갱신 또는 즉시 STOP을 DeskController에 위임한다."""

    dashboard = get_dashboard()
    if isinstance(command, HoldControlRequest):
        return await _run(lambda: dashboard.hold(command.direction))
    assert isinstance(command, StopControlRequest)
    return await _run(
        lambda: dashboard.stop_motion("대시보드에서 수동 이동을 정지했습니다.")
    )


@router.post("/target", response_model=DashboardStatusResponse)
async def target(command: TargetRequest) -> DashboardStatusResponse:
    """자동 목표를 설정하거나 현재 이동을 취소한다."""

    dashboard = get_dashboard()
    if isinstance(command, SetTargetRequest):
        return await _run(lambda: dashboard.set_target(command.target_cm))
    assert isinstance(command, CancelTargetRequest)
    return await _run(dashboard.cancel_target)
