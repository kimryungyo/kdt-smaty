"""프로필 SQLite CRUD HTTP route다."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TypeVar

from fastapi import APIRouter, Header, HTTPException, Response, status

from smart_desk.core.container import get_container
from smart_desk.modules.dashboard import get_dashboard
from smart_desk.modules.identity.models import SessionKind
from smart_desk.modules.identity.service import EnrollmentConflictError
from smart_desk.modules.profiles.models import (
    Profile,
    ProfileCreate,
    ProfilePin,
    ProfileUpdate,
)
from smart_desk.modules.profiles.pin import hash_pin, verify_pin
from smart_desk.modules.profiles.repository import (
    ProfileConflictError,
    ProfileNotFoundError,
    ProfileRepositoryError,
)
from smart_desk.storage import StorageError
from smart_desk.modules.assistant.memory import ProfileMemoryError


router = APIRouter(prefix="/api/profiles", tags=["profiles"])
Result = TypeVar("Result")


async def _run(operation: Callable[[], Awaitable[Result]]) -> Result:
    try:
        return await operation()
    except ProfileNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except ProfileConflictError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except (ProfileRepositoryError, StorageError) as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="프로필 저장소를 현재 사용할 수 없습니다.",
        ) from error


@router.get("", response_model=list[Profile])
async def list_profiles() -> list[Profile]:
    return await _run(get_dashboard().list_profiles)


@router.get("/{profile_id}", response_model=Profile)
async def get_profile(profile_id: str) -> Profile:
    return await _run(lambda: get_dashboard().get_profile(profile_id))


@router.post("", response_model=Profile, status_code=status.HTTP_201_CREATED)
async def create_profile(create: ProfileCreate) -> Profile:
    return await _run(lambda: get_dashboard().create_profile(create))


async def _is_current_user(profile_id: str) -> bool:
    """얼굴로 인식된 본인인지 확인한다. 본인 자리에서는 PIN을 묻지 않는다."""

    service = get_container().current_user
    if service is None:
        return False
    snapshot = await service.snapshot()
    return (
        snapshot is not None
        and snapshot.kind is SessionKind.REGISTERED
        and snapshot.profile_id == profile_id
    )


async def _require_pin(profile_id: str, supplied: str | None) -> None:
    """현재 인식된 본인이 아니면 PIN 없이는 프로필을 건드리지 못하게 막는다."""

    stored = await _run(lambda: get_container().profiles.get_pin_hash(profile_id))
    if stored is None or await _is_current_user(profile_id):
        return
    if supplied is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="현재 사용자가 아닌 프로필은 PIN을 입력해야 수정할 수 있습니다.",
        )
    if not verify_pin(supplied, stored):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="PIN이 일치하지 않습니다.",
        )


@router.put("/{profile_id}/pin", status_code=status.HTTP_204_NO_CONTENT)
async def set_profile_pin(
    profile_id: str,
    body: ProfilePin,
    x_profile_pin: str | None = Header(default=None),
) -> Response:
    """등록 마무리와 PIN 변경에 쓴다. 이미 PIN이 있으면 기존 PIN을 요구한다."""

    await _run(lambda: get_dashboard().get_profile(profile_id))
    await _require_pin(profile_id, x_profile_pin)
    await _run(
        lambda: get_container().profiles.set_pin_hash(profile_id, hash_pin(body.pin))
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{profile_id}/pin/verify", status_code=status.HTTP_204_NO_CONTENT)
async def verify_profile_pin(profile_id: str, body: ProfilePin) -> Response:
    """수정 화면에 들어가기 전에 PIN을 확인한다."""

    stored = await _run(lambda: get_container().profiles.get_pin_hash(profile_id))
    if stored is not None and not verify_pin(body.pin, stored):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="PIN이 일치하지 않습니다.",
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch("/{profile_id}", response_model=Profile)
async def update_profile(
    profile_id: str,
    update: ProfileUpdate,
    x_profile_pin: str | None = Header(default=None),
) -> Profile:
    # 본인 자리(얼굴 인식된 프로필)는 _require_pin이 그대로 통과시키므로,
    # 대시보드가 높이·LED를 저장하는 경로는 PIN을 묻지 않는다.
    await _require_pin(profile_id, x_profile_pin)
    return await _run(lambda: get_dashboard().update_profile(profile_id, update))


@router.delete("/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_profile(
    profile_id: str,
    x_profile_pin: str | None = Header(default=None),
) -> Response:
    await _run(lambda: get_dashboard().get_profile(profile_id))
    await _require_pin(profile_id, x_profile_pin)
    container = get_container()
    memory = container.profile_memory
    if memory is not None:
        try:
            await memory.delete_profile(profile_id)
        except ProfileMemoryError as error:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "프로필 기억을 삭제할 수 없습니다.",
            ) from error
    identity = container.identity
    if identity is not None:
        try:
            await identity.prepare_profile_delete(profile_id)
        except EnrollmentConflictError as error:
            raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    try:
        await _run(lambda: get_dashboard().delete_profile(profile_id))
    except BaseException:
        if identity is not None:
            await identity.abort_profile_delete(profile_id)
        raise
    if identity is not None:
        await identity.finalize_profile_delete(profile_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
