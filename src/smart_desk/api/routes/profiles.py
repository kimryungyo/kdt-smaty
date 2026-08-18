"""프로필 SQLite CRUD HTTP route다."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TypeVar

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field

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


class ProfileMemoryResponse(BaseModel):
    """Profile-scoped, content-bearing memory management response."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    memory: str
    created_at: datetime | None = None
    updated_at: datetime | None = None
    metadata: dict[str, Any] | None = None


class ProfileMemoryUpdate(BaseModel):
    memory: str = Field(min_length=1, max_length=500)


class ProfileMemoryCreate(BaseModel):
    memory: str = Field(min_length=1, max_length=500)


def _memory_response(value: dict[str, Any]) -> ProfileMemoryResponse:
    return ProfileMemoryResponse(
        id=value["id"],
        memory=value["memory"],
        created_at=value.get("created_at"),
        updated_at=value.get("updated_at"),
        metadata=value.get("metadata") if isinstance(value.get("metadata"), dict) else None,
    )


async def _memory_service() -> Any:
    memory = get_container().profile_memory
    if memory is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "프로필 기억을 사용할 수 없습니다.")
    return memory


def _memory_failure(error: ProfileMemoryError) -> HTTPException:
    if error.code == "profile_memory_not_found":
        return HTTPException(status.HTTP_404_NOT_FOUND, "프로필 기억을 찾을 수 없습니다.")
    if error.code == "profile_memory_policy_rejected":
        return HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "저장할 수 없는 기억입니다.")
    return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "프로필 기억을 현재 사용할 수 없습니다.")


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


async def _require_pin(
    profile_id: str,
    supplied: str | None,
    *,
    allow_current_user: bool = True,
) -> None:
    """PIN 없이는 프로필을 건드리지 못하게 막는다.

    `allow_current_user`가 True면 얼굴로 인식된 본인은 그냥 통과시킨다. 삭제처럼
    되돌릴 수 없는 작업은 본인이어도 PIN을 받도록 False로 호출한다.
    """

    stored = await _run(lambda: get_container().profiles.get_pin_hash(profile_id))
    if stored is None:
        return
    if allow_current_user and await _is_current_user(profile_id):
        return
    if supplied is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="이 작업에는 프로필 PIN이 필요합니다.",
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


@router.get("/{profile_id}/memories", response_model=list[ProfileMemoryResponse])
async def list_profile_memories(
    profile_id: str, x_profile_pin: str | None = Header(default=None)
) -> list[ProfileMemoryResponse]:
    await _run(lambda: get_dashboard().get_profile(profile_id))
    await _require_pin(profile_id, x_profile_pin)
    try:
        values = await (await _memory_service()).list_profile(profile_id)
    except ProfileMemoryError as error:
        raise _memory_failure(error) from error
    return [_memory_response(value) for value in values]


@router.post("/{profile_id}/memories", status_code=status.HTTP_204_NO_CONTENT)
async def create_profile_memory(
    profile_id: str,
    body: ProfileMemoryCreate,
    x_profile_pin: str | None = Header(default=None),
) -> Response:
    """Store one operator-confirmed fact without exposing the Mem0 SDK to clients."""

    await _run(lambda: get_dashboard().get_profile(profile_id))
    await _require_pin(profile_id, x_profile_pin)
    try:
        await (await _memory_service()).remember(
            profile_id,
            body.memory,
            explicit=True,
            source="explicit_dashboard",
            infer=False,
        )
    except ProfileMemoryError as error:
        raise _memory_failure(error) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch("/{profile_id}/memories/{memory_id}", response_model=ProfileMemoryResponse)
async def update_profile_memory(
    profile_id: str,
    memory_id: str,
    body: ProfileMemoryUpdate,
    x_profile_pin: str | None = Header(default=None),
) -> ProfileMemoryResponse:
    await _run(lambda: get_dashboard().get_profile(profile_id))
    await _require_pin(profile_id, x_profile_pin)
    try:
        value = await (await _memory_service()).update(profile_id, memory_id, body.memory)
    except ProfileMemoryError as error:
        raise _memory_failure(error) from error
    return _memory_response(value)


@router.delete("/{profile_id}/memories/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_profile_memory(
    profile_id: str,
    memory_id: str,
    x_profile_pin: str | None = Header(default=None),
) -> Response:
    await _run(lambda: get_dashboard().get_profile(profile_id))
    await _require_pin(profile_id, x_profile_pin)
    try:
        await (await _memory_service()).delete(profile_id, memory_id)
    except ProfileMemoryError as error:
        raise _memory_failure(error) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_profile(
    profile_id: str,
    x_profile_pin: str | None = Header(default=None),
) -> Response:
    await _run(lambda: get_dashboard().get_profile(profile_id))
    # 삭제는 얼굴·작업 모드·기억까지 함께 지우고 되돌릴 수 없으므로 본인이어도
    # PIN을 받는다.
    await _require_pin(profile_id, x_profile_pin, allow_current_user=False)
    container = get_container()
    memory = container.profile_memory
    if memory is not None:
        begin = getattr(memory, "begin_profile_deletion", None)
        if callable(begin):
            await begin(profile_id)
        try:
            await memory.delete_profile(profile_id)
        except ProfileMemoryError as error:
            abort = getattr(memory, "abort_profile_deletion", None)
            if callable(abort):
                await abort(profile_id)
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
        if memory is not None:
            abort = getattr(memory, "abort_profile_deletion", None)
            if callable(abort):
                await abort(profile_id)
        raise
    if identity is not None:
        await identity.finalize_profile_delete(profile_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
