"""프로필 모델, repository와 container accessor를 노출한다."""

from smart_desk.core.container import get_container
from smart_desk.modules.profiles.models import Profile, ProfileCreate, ProfileUpdate
from smart_desk.modules.profiles.repository import (
    ProfileConflictError,
    ProfileNotFoundError,
    ProfileRepository,
    ProfileRepositoryError,
    generate_profile_id,
)


def get_profiles() -> ProfileRepository:
    """AppContainer가 소유한 프로필 repository를 반환한다."""

    return get_container().profiles


__all__ = [
    "Profile",
    "ProfileConflictError",
    "ProfileCreate",
    "ProfileNotFoundError",
    "ProfileRepository",
    "ProfileRepositoryError",
    "ProfileUpdate",
    "generate_profile_id",
    "get_profiles",
]
