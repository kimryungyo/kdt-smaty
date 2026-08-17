"""WLED 상태와 전체 조명 제어 HTTP route다."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from smart_desk.core.container import get_container

from smart_desk.modules.wled import (
    WledDisabledError,
    WledError,
    WledProtocolError,
    WledUnavailableError,
    WledSessionMismatchError,
    WledUnsupportedValueError,
    get_wled,
)
from smart_desk.modules.wled.models import (
    BrightnessControlRequest,
    ControlRequest,
    EffectControlRequest,
    OffControlRequest,
    SolidControlRequest,
    WledCapabilitiesResponse,
    WledSnapshotResponse,
    snapshot_response,
    capabilities_response,
)


router = APIRouter(prefix="/api/wled", tags=["wled"])


def _error(error: WledError) -> HTTPException:
    if isinstance(error, WledSessionMismatchError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "SESSION_MISMATCH", "refresh": True},
        )
    if isinstance(error, WledUnsupportedValueError):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error))
    if isinstance(error, WledProtocolError):
        return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="WLED 장치 응답이 올바르지 않습니다.")
    return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="WLED 장치를 현재 사용할 수 없습니다.")


@router.get("/status", response_model=WledSnapshotResponse)
async def get_status() -> WledSnapshotResponse:
    try:
        client = get_wled()
    except WledDisabledError:
        from smart_desk.modules.wled.models import WledSnapshot, WledStatus
        return snapshot_response(WledSnapshot(WledStatus.DISABLED, None, None, None, None, None, None, None, None, None, None, None))
    try:
        return snapshot_response(await client.refresh_state())
    except WledError:
        return snapshot_response(client.get_snapshot())


@router.get("/capabilities", response_model=WledCapabilitiesResponse)
async def get_capabilities() -> WledCapabilitiesResponse:
    try:
        return capabilities_response(await get_wled().refresh_capabilities())
    except WledError as error:
        raise _error(error) from error


@router.post("/control", response_model=WledSnapshotResponse)
async def control(command: ControlRequest) -> WledSnapshotResponse:
    try:
        client = get_wled()
        expected = command.expected_session_id
        if expected is not None:
            current_user = get_container().current_user
            current = await current_user.snapshot() if current_user is not None else None
            if current is None or current.session_id != expected:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "SESSION_MISMATCH",
                        "message": "현재 사용자 세션이 변경되었습니다.",
                        "currentSessionId": current.session_id if current else None,
                        "refresh": True,
                    },
                )
        if isinstance(command, SolidControlRequest):
            result = await client.set_solid(command.color, expected_session_id=expected)
        elif isinstance(command, EffectControlRequest):
            result = await client.set_effect(
                command.effect_id,
                palette_id=command.palette_id,
                speed=command.speed,
                intensity=command.intensity,
                color=command.color,
                expected_session_id=expected,
            )
        elif isinstance(command, BrightnessControlRequest):
            result = await client.set_brightness(
                command.brightness, expected_session_id=expected
            )
        else:
            assert isinstance(command, OffControlRequest)
            result = await client.turn_off(expected_session_id=expected)
        return snapshot_response(result)
    except WledError as error:
        raise _error(error) from error
