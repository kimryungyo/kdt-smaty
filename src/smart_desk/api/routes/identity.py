"""Read-only current-user and face enrollment HTTP endpoints."""

from fastapi import APIRouter, HTTPException, Response, status

from smart_desk.core.container import get_container
from smart_desk.modules.identity import get_current_user, get_identity
from smart_desk.modules.identity.models import (
    CurrentUserApi,
    CurrentUserResponse,
    CurrentUserSnapshot,
    EnrollmentSnapshot,
)
from smart_desk.modules.identity.service import (
    EnrollmentConflictError,
    FreshSingleFaceRequiredError,
    ModelUnavailableError,
)
from smart_desk.modules.identity.repository import FaceEmbeddingRepositoryError
from smart_desk.modules.profiles.repository import ProfileNotFoundError, ProfileRepositoryError
from smart_desk.storage import StorageError


router = APIRouter(tags=["identity"])


def _session_response(snapshot: CurrentUserSnapshot | None) -> CurrentUserResponse:
    if snapshot is None:
        return CurrentUserResponse(session=None)
    return CurrentUserResponse(
        session=CurrentUserApi(
            session_id=snapshot.session_id,
            kind=snapshot.kind,
            profile_id=snapshot.profile_id,
            started_at=snapshot.started_at,
            changed_at=snapshot.changed_at,
        )
    )


@router.get("/api/current-user", response_model=CurrentUserResponse)
async def current_user() -> CurrentUserResponse:
    return _session_response(await get_current_user().snapshot())


@router.post(
    "/api/profiles/{profile_id}/face-enrollments",
    response_model=EnrollmentSnapshot,
    status_code=status.HTTP_202_ACCEPTED,
)
async def enroll(profile_id: str) -> EnrollmentSnapshot:
    try:
        await get_container().profiles.get_profile(profile_id)
        return await get_identity().start_enrollment(profile_id)
    except ProfileNotFoundError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
    except ModelUnavailableError as error:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(error)) from error
    except FreshSingleFaceRequiredError as error:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(error)) from error
    except EnrollmentConflictError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    except (FaceEmbeddingRepositoryError, ProfileRepositoryError, StorageError) as error:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(error)) from error


@router.get("/api/face-enrollments/{enrollment_id}", response_model=EnrollmentSnapshot)
async def enrollment(enrollment_id: str) -> EnrollmentSnapshot:
    snapshot = await get_identity().enrollment(enrollment_id)
    if snapshot is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "얼굴 등록을 찾을 수 없습니다.")
    return snapshot


@router.delete("/api/face-enrollments/{enrollment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel(enrollment_id: str) -> Response:
    try:
        cancelled = await get_identity().cancel(enrollment_id)
    except EnrollmentConflictError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    if not cancelled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "얼굴 등록을 찾을 수 없습니다.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/api/profiles/{profile_id}/face", status_code=status.HTTP_204_NO_CONTENT)
async def delete_face(profile_id: str) -> Response:
    try:
        await get_container().profiles.get_profile(profile_id)
    except ProfileNotFoundError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
    except (ProfileRepositoryError, StorageError) as error:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(error)) from error
    try:
        await get_identity().delete_face(profile_id)
    except EnrollmentConflictError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    except (FaceEmbeddingRepositoryError, ProfileRepositoryError, StorageError) as error:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(error)) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)
