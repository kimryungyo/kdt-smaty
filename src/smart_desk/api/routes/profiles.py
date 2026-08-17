"""프로필 SQLite CRUD HTTP route다."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TypeVar

from fastapi import APIRouter, HTTPException, Response, status

from smart_desk.modules.dashboard import get_dashboard
from smart_desk.modules.profiles.models import Profile, ProfileCreate, ProfileUpdate
from smart_desk.modules.profiles.repository import (
    ProfileConflictError,
    ProfileNotFoundError,
    ProfileRepositoryError,
)
from smart_desk.storage import StorageError


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


@router.patch("/{profile_id}", response_model=Profile)
async def update_profile(profile_id: str, update: ProfileUpdate) -> Profile:
    return await _run(lambda: get_dashboard().update_profile(profile_id, update))


@router.delete("/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_profile(profile_id: str) -> Response:
    await _run(lambda: get_dashboard().delete_profile(profile_id))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
