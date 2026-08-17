from smart_desk.core.container import get_container

from .models import CurrentUserSnapshot
from .session import CurrentUserSessionService
from .service import FaceIdentityService, UnavailableFaceEmbeddingExtractor
from .opencv import OpenCvSFaceEmbeddingExtractor


def get_identity() -> FaceIdentityService:
    identity = get_container().identity
    if identity is None:
        raise RuntimeError("Face identity service가 조립되지 않았습니다.")
    return identity


def get_current_user() -> CurrentUserSessionService:
    current_user = get_container().current_user
    if current_user is None:
        raise RuntimeError("Current user service가 조립되지 않았습니다.")
    return current_user


__all__ = [
    "CurrentUserSnapshot",
    "FaceIdentityService",
    "OpenCvSFaceEmbeddingExtractor",
    "UnavailableFaceEmbeddingExtractor",
    "get_current_user",
    "get_identity",
]
